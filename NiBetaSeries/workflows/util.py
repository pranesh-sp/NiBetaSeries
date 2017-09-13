#!/usr/bin/env python
# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
'''
Workflow for getting subject info
-presumes fmriprep has run, expects directories to both BIDS data and fmriprep output
'''
from __future__ import print_function, division, absolute_import, unicode_literals

from bids.grabbids import BIDSLayout
from nipype.interfaces.base import Bunch
from nipype.interfaces.utility import IdentityInterface
import nipype.interfaces.io as nio
from niworkflows.nipype import logging
import pandas as pd
import numpy as np

def get_bids_data(bids_dir):
    return BIDSLayout(bids_dir)

# TODO: incorporate run into analysis
def get_bold_info(bids_data, task, subject, run=None):
    import json
    from collections import Counter
    logger = logging.getLogger('interface')

    # get the TR
    global_json_list = bids_data.get(extensions='task-'+task+'_bold.json',
                                     type='bold',
                                     task=task,
                                     return_type='file')

    if len(global_json_list) > 1:
        logger.error('ERROR: More than one global json for the task')
    elif len(global_json_list) == 0:
        logger.warn('WARNING: No global json for the task')
        global_json = None
    else:
        with open(global_json_list[0]) as glob_info:
            global_json = Counter(json.load(glob_info))

    subject_json_list = bids_data.get(extensions='json',
                                      type='bold',
                                      task=task,
                                      subject=subject,
                                      return_type='file')

    if len(subject_json_list) > 1:
        logger.error('ERROR: More than one subject json for the task')
    elif len(global_json_list) == 0:
        logger.warn('WARNING: No subject json for the task')
        subject_json = None
    else:
        with open(subject_json_list[0]) as sub_info:
            subject_json = Counter(json.load(sub_info))

    if subject_json is not None and global_json is not None:
        bold_parameters = subject_json + global_json
    elif subject_json is not None:
        bold_parameters = subject_json
    elif global_json is not None:
        bold_parameters = global_json

    repetition_time = bold_parameters['RepetitionTime']

    return repetition_time


def get_confound_info(derivatives_data, task, subject, run=None, regressor_names=None):
    logger = logging.getLogger('interface')

    if run is not None:
        task_confounds_list = bids_data.get(extensions='task-'+task+'_confounds.tsv',
                                            type='confounds',
                                            task=task,
                                            run=run,
                                            return_type='file')
    else:
        task_confounds_list = bids_data.get(extensions='task-'+task+'_confounds.tsv',
                                            type='confounds',
                                            task=task,
                                            return_type='file')

    # There should exactly one confound file
    if len(task_confounds_list) > 1:
        logger.error('ERROR: More than one confound tsv for the task')
    elif len(task_confounds_list) == 0:
        logger.error('ERROR: No confound tsv for the task')
    else:
        confounds_pd = pd.read_csv(task_confounds_list[0], sep="\t")

    if regressor_names == None:
        confound_names = [col for col in confound_pd.columns
                             if 'CompCor' in col or 'X' in col or 'Y' in col or 'Z' in col]
        confound_pd_filt = confound_pd.filter(items=confound_names)

        confounds = confound_pd_filt.values.swapaxes(0,1).tolist()

    return confound_names, confounds


def get_task_info(bids_data, task, subject, run=None):
    logger = logging.getLogger('interface')

    if run is not None:
        task_event_list = bids_data.get(extensions='tsv',
                                            type='events',
                                            task=task,
                                            run=run,
                                            return_type='file')
    else:
        task_event_list = bids_data.get(extensions='tsv',
                                            type='events',
                                            task=task,
                                            return_type='file')

    if len(task_event_list) > 1:
        logger.error('ERROR: More than one event tsv for the task')
    elif len(task_event_list) == 0:
        logger.error('ERROR: No event tsv for the task')
    else:
        events_pd = pd.read_csv(task_event_list[0], sep="\t")

    events_pd_grouped = events_pd[['onset', 'duration', 'trial_type']].groupby('trial_type')

    conditions = list(events_pd_grouped.groups.keys())
    onsets = [ events_pd_grouped.get_group(condition)[['onset']].values.flatten().tolist() ]
    durations = [ events_pd_grouped.get_group(condition)[['duration']].values.flatten().tolist() ]

    return conditions, onsets, durations

def bunch_info(conditions, onsets, durations, regressor_names, regressors):
    return Bunch(conditions=conditions,
                onsets=onsets,
                durations=durations,
                regressors=regressors,
                regressor_names=regressor_names)

def init_subject_info_wf(name='subject_info_wf'):
    input_node = pe.Node(IdentityInterface(fields=['subject', 'bids_dir', 'hrf_model', 'fmriprep_dir']),
                         name='input_node')

    output_node = pe.Node(IdentityInterface(fields=['subject_info', 'input_units',
                                                     'time_repetition'])
                           name='output_node')

    bids_data_node = pe.Node(
        utility.Function(function=get_bids_data,
                         input_names=['bids_dir'],
                         output_names=['bids_data']),
        name='bids_data_node')

    confound_info_node = pe.Node(
        utility.Function(function=get_confound_info,
                         input_names=['derivatives_data', 'task', 'subject', 'run'],
                         output_names=['confound_names', 'confounds']),
        name='confound_info_node')

    # TODO: get tasks
    confound_info_node.iterables = ("task", tasks)

class BIDSError(ValueError):
    def __init__(self, message, bids_root):
        indent = 10
        header = '{sep} BIDS root folder: "{bids_root}" {sep}'.format(
            bids_root=bids_root, sep=''.join(['-'] * indent))
        self.msg = '\n{header}\n{indent}{message}\n{footer}'.format(
            header=header, indent=''.join([' '] * (indent + 1)),
            message=message, footer=''.join(['-'] * len(header))
        )
        super(BIDSError, self).__init__(self.msg)
        self.bids_root = bids_root


class BIDSWarning(RuntimeWarning):
    pass


def collect_participants(bids_dir, participant_label=None, strict=False):
    """
    List the participants under the BIDS root and checks that participants
    designated with the participant_label argument exist in that folder.
    Returns the list of participants to be finally processed.
    Requesting all subjects in a BIDS directory root:
    >>> collect_participants('ds114')
    ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10']
    Requesting two subjects, given their IDs:
    >>> collect_participants('ds114', participant_label=['02', '04'])
    ['02', '04']
    Requesting two subjects, given their IDs (works with 'sub-' prefixes):
    >>> collect_participants('ds114', participant_label=['sub-02', 'sub-04'])
    ['02', '04']
    Requesting two subjects, but one does not exist:
    >>> collect_participants('ds114', participant_label=['02', '14'])
    ['02']
    >>> collect_participants('ds114', participant_label=['02', '14'],
    ...                      strict=True)  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    fmriprep.utils.bids.BIDSError:
    ...
    """
    bids_dir = op.abspath(bids_dir)
    all_participants = sorted(
        [subdir[4:] for subdir in os.listdir(bids_dir)
         if op.isdir(op.join(bids_dir, subdir)) and subdir.startswith('sub-')])

    # Error: bids_dir does not contain subjects
    if not all_participants:
        raise BIDSError(
            'Could not find participants. Please make sure the BIDS data '
            'structure is present and correct. Datasets can be validated online '
            'using the BIDS Validator (http://incf.github.io/bids-validator/).\n'
            'If you are using Docker for Mac or Docker for Windows, you '
            'may need to adjust your "File sharing" preferences.', bids_dir)

    # No --participant-label was set, return all
    if participant_label is None or not participant_label:
        return all_participants

    if isinstance(participant_label, str):
        participant_label = [participant_label]

    # Drop sub- prefixes
    participant_label = [sub[4:] if sub.startswith('sub-') else sub for sub in participant_label]
    # Remove duplicates
    participant_label = sorted(set(participant_label))
    # Remove labels not found
    found_label = sorted(set(participant_label) & set(all_participants))
    if not found_label:
        raise BIDSError('Could not find participants [{}]'.format(
            ', '.join(participant_label)), bids_dir)

    # Warn if some IDs were not found
    notfound_label = sorted(set(participant_label) - set(all_participants))
    if notfound_label:
        exc = BIDSError('Some participants were not found: {}'.format(
            ', '.join(notfound_label)), bids_dir)
        if strict:
            raise exc
        warnings.warn(exc.msg, BIDSWarning)

    return found_label


def collect_data(dataset, participant_label, task=None):
    """
    Uses grabbids to retrieve the input data for a given participant
    >>> bids_root, _ = collect_data('ds054', '100185')
    >>> bids_root['fmap']  # doctest: +ELLIPSIS
    ['.../ds054/sub-100185/fmap/sub-100185_magnitude1.nii.gz', \
'.../ds054/sub-100185/fmap/sub-100185_magnitude2.nii.gz', \
'.../ds054/sub-100185/fmap/sub-100185_phasediff.nii.gz']
    >>> bids_root['bold']  # doctest: +ELLIPSIS
    ['.../ds054/sub-100185/func/sub-100185_task-machinegame_run-01_bold.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-02_bold.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-03_bold.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-04_bold.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-05_bold.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-06_bold.nii.gz']
    >>> bids_root['sbref']  # doctest: +ELLIPSIS
    ['.../ds054/sub-100185/func/sub-100185_task-machinegame_run-01_sbref.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-02_sbref.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-03_sbref.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-04_sbref.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-05_sbref.nii.gz', \
'.../ds054/sub-100185/func/sub-100185_task-machinegame_run-06_sbref.nii.gz']
    >>> bids_root['t1w']  # doctest: +ELLIPSIS
    ['.../ds054/sub-100185/anat/sub-100185_T1w.nii.gz']
    >>> bids_root['t2w']  # doctest: +ELLIPSIS
    []
    """
    layout = BIDSLayout(dataset)
    queries = {
        'fmap': {'subject': participant_label, 'modality': 'fmap',
                 'extensions': ['nii', 'nii.gz']},
        'bold': {'subject': participant_label, 'modality': 'func', 'type': 'bold',
                 'extensions': ['nii', 'nii.gz']},
        'sbref': {'subject': participant_label, 'modality': 'func', 'type': 'sbref',
                  'extensions': ['nii', 'nii.gz']},
        't2w': {'subject': participant_label, 'type': 'T2w',
                'extensions': ['nii', 'nii.gz']},
        't1w': {'subject': participant_label, 'type': 'T1w',
                'extensions': ['nii', 'nii.gz']},
    }

    if task:
        queries['bold']['task'] = task

    return {modality: [x.filename for x in layout.get(**query)]
            for modality, query in queries.items()}, layout
# class ReadSidecarJSONInputSpec(BaseInterfaceInputSpec):
#     in_file = File(exists=True, mandatory=True, desc='the input nifti file')
#     fields = traits.List(traits.Str, desc='get only certain fields')
#
#
# class ReadSidecarJSONOutputSpec(TraitedSpec):
#     subject_id = traits.Str()
#     session_id = traits.Str()
#     task_id = traits.Str()
#     acq_id = traits.Str()
#     rec_id = traits.Str()
#     run_id = traits.Str()
#     out_dict = traits.Dict()
#
#
# class ReadSidecarJSON(SimpleInterface):
#     """
#     An utility to find and read JSON sidecar files of a BIDS tree
#     """
#     expr = re.compile('^sub-(?P<subject_id>[a-zA-Z0-9]+)(_ses-(?P<session_id>[a-zA-Z0-9]+))?'
#                       '(_task-(?P<task_id>[a-zA-Z0-9]+))?(_acq-(?P<acq_id>[a-zA-Z0-9]+))?'
#                       '(_rec-(?P<rec_id>[a-zA-Z0-9]+))?(_run-(?P<run_id>[a-zA-Z0-9]+))?')
#     input_spec = ReadSidecarJSONInputSpec
#     output_spec = ReadSidecarJSONOutputSpec
#     _always_run = True
#
#     def _run_interface(self, runtime):
#         metadata = get_metadata_for_nifti(self.inputs.in_file)
#         output_keys = [key for key in list(self.output_spec().get().keys()) if key.endswith('_id')]
#         outputs = self.expr.search(op.basename(self.inputs.in_file)).groupdict()
#
#         for key in output_keys:
#             id_value = outputs.get(key)
#             if id_value is not None:
#                 self._results[key] = outputs.get(key)
#
#         if isdefined(self.inputs.fields) and self.inputs.fields:
#             for fname in self.inputs.fields:
#                 self._results[fname] = metadata[fname]
#         else:
#             self._results['out_dict'] = metadata
#
#         return runtime
