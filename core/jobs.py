# coding: utf-8
#
# Copyright 2014 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Common classes and methods for managing long running jobs."""

__author__ = 'Sean Lip'

import ast
import copy
import datetime
import logging
import traceback
import utils

from core.platform import models
(job_models,) = models.Registry.import_models([models.NAMES.job])
taskqueue_services = models.Registry.import_taskqueue_services()
transaction_services = models.Registry.import_transaction_services()

from google.appengine.ext import ndb

from mapreduce import base_handler
from mapreduce import context
from mapreduce import input_readers
from mapreduce import mapreduce_pipeline
from mapreduce.lib.pipeline import pipeline
from mapreduce import util as mapreduce_util

MAPPER_PARAM_KEY_ENTITY_KINDS = 'entity_kinds'
MAPPER_PARAM_KEY_QUEUED_TIME_MSECS = 'queued_time_msecs'

STATUS_CODE_NEW = job_models.STATUS_CODE_NEW
STATUS_CODE_QUEUED = job_models.STATUS_CODE_QUEUED
STATUS_CODE_STARTED = job_models.STATUS_CODE_STARTED
STATUS_CODE_COMPLETED = job_models.STATUS_CODE_COMPLETED
STATUS_CODE_FAILED = job_models.STATUS_CODE_FAILED
STATUS_CODE_CANCELED = job_models.STATUS_CODE_CANCELED

VALID_STATUS_CODE_TRANSITIONS = {
    STATUS_CODE_NEW: [STATUS_CODE_QUEUED],
    STATUS_CODE_QUEUED: [STATUS_CODE_STARTED, STATUS_CODE_CANCELED],
    STATUS_CODE_STARTED: [
        STATUS_CODE_COMPLETED, STATUS_CODE_FAILED, STATUS_CODE_CANCELED],
    STATUS_CODE_COMPLETED: [],
    STATUS_CODE_FAILED: [],
    STATUS_CODE_CANCELED: [],
}

# The default amount of time that defines a 'recent' job. Jobs that were
# queued more recently than this number of milliseconds ago are considered
# 'recent'.
DEFAULT_RECENCY_MSEC = 14 * 24 * 60 * 60 * 1000


class BaseJobManager(object):
    """Base class for managing long-running jobs.

    These jobs are not transaction-safe, and multiple jobs of the same kind
    may run at once and overlap. Individual jobs should account for this. In
    particular, if a job writes to some location, no other enqueued or running
    job should be writing to, or reading from, that location.

    This is expected to be the case for one-off migration jobs, as well as
    batch reporting jobs. One-off migration jobs are expected to be transient
    and will not be a permanent part of the codebase. Batch reporting jobs are
    expected to write to a particular datastore model that is optimized for
    fast querying; each batch reporting job should correspond to exactly one of
    these models. The reporting jobs are expected to be run as MapReduces; to
    find existing ones, search for subclasses of BaseMapReduceJobManager.

    Note that the enqueue(), register_start(), register_completion(),
    register_failure() and cancel() methods in this class batch the following
    operations: (a) pre- and post-hooks, (b) updating the status of the job in
    the datastore, and (c) actually performing the operation. Each entire batch
    is not run in a transaction, but subclasses can still perform (a) or (c)
    transactionally if they wish to.
    """
    @classmethod
    def _is_abstract(cls):
        return cls in ABSTRACT_BASE_CLASSES

    @classmethod
    def create_new(cls):
        """Creates a new job of this class type. Returns the id of this job."""
        if cls._is_abstract():
            raise Exception(
                'Tried to directly create a job using the abstract base '
                'manager class %s, which is not allowed.' % cls.__name__)

        def _create_new_job():
            job_id = job_models.JobModel.get_new_id(cls.__name__)
            job_models.JobModel(id=job_id, job_type=cls.__name__).put()
            return job_id

        return transaction_services.run_in_transaction(_create_new_job)

    @classmethod
    def enqueue(cls, job_id):
        """Marks a job as queued and adds it to a queue for processing."""
        # Ensure that preconditions are met.
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_valid_transition(
            job_id, model.status_code, STATUS_CODE_QUEUED)
        cls._require_correct_job_type(model.job_type)

        # Enqueue the job.
        cls._pre_enqueue_hook(job_id)
        cls._real_enqueue(job_id)

        model.status_code = STATUS_CODE_QUEUED
        model.time_queued_msec = utils.get_current_time_in_millisecs()
        model.put()

        cls._post_enqueue_hook(job_id)

    @classmethod
    def register_start(cls, job_id, metadata=None):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_valid_transition(
            job_id, model.status_code, STATUS_CODE_STARTED)
        cls._require_correct_job_type(model.job_type)

        cls._pre_start_hook(job_id)

        model.metadata = metadata
        model.status_code = STATUS_CODE_STARTED
        model.time_started_msec = utils.get_current_time_in_millisecs()
        model.put()

        cls._post_start_hook(job_id)

    @classmethod
    def register_completion(cls, job_id, output):
        """Marks a job as completed."""
        # Ensure that preconditions are met.
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_valid_transition(
            job_id, model.status_code, STATUS_CODE_COMPLETED)
        cls._require_correct_job_type(model.job_type)

        model.status_code = STATUS_CODE_COMPLETED
        model.time_finished_msec = utils.get_current_time_in_millisecs()
        model.output = output
        model.put()

        cls._post_completed_hook(job_id)

    @classmethod
    def register_failure(cls, job_id, error):
        """Marks a job as failed."""
        # Ensure that preconditions are met.
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_valid_transition(
            job_id, model.status_code, STATUS_CODE_FAILED)
        cls._require_correct_job_type(model.job_type)

        model.status_code = STATUS_CODE_FAILED
        model.time_finished_msec = utils.get_current_time_in_millisecs()
        model.error = error
        model.put()

        cls._post_failure_hook(job_id)

    @classmethod
    def cancel(cls, job_id, user_id):
        # Ensure that preconditions are met.
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_valid_transition(
            job_id, model.status_code, STATUS_CODE_CANCELED)
        cls._require_correct_job_type(model.job_type)

        cancel_message = 'Canceled by %s' % (user_id or 'system')

        # Cancel the job.
        cls._pre_cancel_hook(job_id, cancel_message)

        model.status_code = STATUS_CODE_CANCELED
        model.time_finished_msec = utils.get_current_time_in_millisecs()
        model.error = cancel_message
        model.put()

        cls._post_cancel_hook(job_id, cancel_message)

    @classmethod
    def is_active(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.status_code in [STATUS_CODE_QUEUED, STATUS_CODE_STARTED]

    @classmethod
    def has_finished(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.status_code in [STATUS_CODE_COMPLETED, STATUS_CODE_FAILED]

    @classmethod
    def cancel_all_unfinished_jobs(cls, user_id):
        """Cancel all queued or started jobs of this job type."""
        unfinished_job_models = job_models.JobModel.get_unfinished_jobs(
            cls.__name__)
        for model in unfinished_job_models:
            cls.cancel(model.id, user_id)

    @classmethod
    def _real_enqueue(cls, job_id):
        """Does the actual work of enqueueing a job for deferred execution.

        Must be implemented by subclasses.
        """
        raise NotImplementedError(
            'Subclasses of BaseJobManager should implement _real_enqueue().')

    @classmethod
    def get_status_code(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.status_code

    @classmethod
    def get_time_queued_msec(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.time_queued_msec

    @classmethod
    def get_time_started_msec(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.time_started_msec

    @classmethod
    def get_time_finished_msec(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.time_finished_msec

    @classmethod
    def get_metadata(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.metadata

    @classmethod
    def get_output(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.output

    @classmethod
    def get_error(cls, job_id):
        model = job_models.JobModel.get(job_id, strict=True)
        cls._require_correct_job_type(model.job_type)
        return model.error

    @classmethod
    def _require_valid_transition(
            cls, job_id, old_status_code, new_status_code):
        valid_new_status_codes = VALID_STATUS_CODE_TRANSITIONS[old_status_code]
        if new_status_code not in valid_new_status_codes:
            raise Exception(
                'Invalid status code change for job %s: from %s to %s' %
                (job_id, old_status_code, new_status_code))

    @classmethod
    def _require_correct_job_type(cls, job_type):
        if job_type != cls.__name__:
            raise Exception(
                'Invalid job type %s for class %s' % (job_type, cls.__name__))

    @classmethod
    def _pre_enqueue_hook(cls, job_id):
        pass

    @classmethod
    def _post_enqueue_hook(cls, job_id):
        pass

    @classmethod
    def _pre_start_hook(cls, job_id):
        pass

    @classmethod
    def _post_start_hook(cls, job_id):
        pass

    @classmethod
    def _post_completed_hook(cls, job_id):
        pass

    @classmethod
    def _post_failure_hook(cls, job_id):
        pass

    @classmethod
    def _pre_cancel_hook(cls, job_id, cancel_message):
        pass

    @classmethod
    def _post_cancel_hook(cls, job_id, cancel_message):
        pass


class BaseDeferredJobManager(BaseJobManager):

    @classmethod
    def _run(cls):
        """Function that performs the main business logic of the job.

        Needs to be implemented by subclasses.
        """
        raise NotImplementedError

    @classmethod
    def _run_job(cls, job_id):
        """Starts the job."""
        logging.info(
            'Job %s started at %s' %
            (job_id, utils.get_current_time_in_millisecs()))
        cls.register_start(job_id)

        try:
            result = cls._run()
        except Exception as e:
            logging.error(traceback.format_exc())
            logging.error(
                'Job %s failed at %s' %
                (job_id, utils.get_current_time_in_millisecs()))
            cls.register_failure(
                job_id, '%s\n%s' % (unicode(e), traceback.format_exc()))
            raise taskqueue_services.PermanentTaskFailure(
                'Task failed: %s\n%s' % (unicode(e), traceback.format_exc()))

        # Note that the job may have been canceled after it started and before
        # it reached this stage. This will result in an exception when the
        # validity of the status code transition is checked.
        cls.register_completion(job_id, result)
        logging.info(
            'Job %s completed at %s' %
            (job_id, utils.get_current_time_in_millisecs()))

    @classmethod
    def _real_enqueue(cls, job_id):
        taskqueue_services.defer(cls._run_job, job_id)


class MapReduceJobPipeline(base_handler.PipelineBase):

    def run(self, job_id, job_class_str, kwargs):
        job_class = mapreduce_util.for_name(job_class_str)
        job_class.register_start(job_id, {
            job_class._OUTPUT_KEY_ROOT_PIPELINE_ID: self.root_pipeline_id
        })

        # TODO(sll): Need try/except/mark-as-canceled here?
        output = yield mapreduce_pipeline.MapreducePipeline(**kwargs)
        yield StoreMapReduceResults(job_id, job_class_str, output)

    def finalized(self):
        # Suppress the default Pipeline behavior of sending email.
        # TODO(sll): Should mark-as-done be here instead?
        pass


class StoreMapReduceResults(base_handler.PipelineBase):

    def run(self, job_id, job_class_str, output):
        job_class = mapreduce_util.for_name(job_class_str)

        try:
            iterator = input_readers.RecordsReader(output, 0)
            results_list = []
            for item in iterator:
                # Map/reduce puts reducer output into blobstore files as a
                # string obtained via "str(result)".  Use AST as a safe
                # alternative to eval() to get the Python object back.
                results_list.append(ast.literal_eval(item))
            job_class.register_completion(job_id, results_list)
        except Exception as e:
            logging.error(traceback.format_exc())
            logging.error(
                'Job %s failed at %s' %
                (job_id, utils.get_current_time_in_millisecs()))
            job_class.register_failure(
                job_id,
                '%s\n%s' % (unicode(e), traceback.format_exc()))


class BaseMapReduceJobManager(BaseJobManager):
    # The output for this job is a list of individual results. Each item in
    # the list will be of whatever type is yielded from the 'reduce' method.
    #
    # The 'metadata' field in the BaseJob representing a MapReduceJob
    # is a dict with one key, _OUTPUT_KEY_ROOT_PIPELINE_ID. The corresponding
    # value is a string representing the ID of the MapReduceJobPipeline
    # as known to the mapreduce/lib/pipeline internals. This is used
    # to generate URLs pointing at the pipeline support UI.
    _OUTPUT_KEY_ROOT_PIPELINE_ID = 'root_pipeline_id'

    @classmethod
    def entity_classes_to_map_over(cls):
        """Return a list of reference to the datastore classes to map over."""
        raise NotImplementedError(
            'Classes derived from BaseMapReduceJobManager must implement '
            'entity_classes_to_map_over()')

    @staticmethod
    def map(item):
        """Implements the map function.  Must be declared @staticmethod.

        Args:
          item: The parameter passed to this function is a single element of
          the type given by entity_class(). This function may yield as many
          times as appropriate (including zero) to return key/value 2-tuples.
          For example, to get a count of all explorations, one might yield
          (exploration.id, 1).
        """
        raise NotImplementedError(
            'Classes derived from BaseMapReduceJobManager must implement map '
            'as a @staticmethod.')

    @staticmethod
    def reduce(key, values):
        """Implements the reduce function.  Must be declared @staticmethod.

        This function should yield whatever it likes; the recommended thing to
        do is emit entities. All emitted outputs from all reducers will be
        collected in an array and set into the output value for the job, so
        don't pick anything huge. If you need something huge, persist it out
        into the datastore instead and return a reference (and dereference it
        later to load content as needed).

        Args:
          key: A key value as emitted from the map() function, above.
          values: A list of all values from all mappers that were tagged with
          the given key. This code can assume that it is the only process
          handling values for this key. (It can probably also assume that
          it will be called exactly once for each key with all of the output,
          but this needs to be verified.)
        """
        raise NotImplementedError(
            'Classes derived from BaseMapReduceJobManager must implement '
            'reduce as a @staticmethod.')

    @classmethod
    def _real_enqueue(cls, job_id):
        entity_class_types = cls.entity_classes_to_map_over()
        entity_class_names = [
            '%s.%s' % (
                entity_class_type.__module__, entity_class_type.__name__)
            for entity_class_type in entity_class_types]

        kwargs = {
            'job_name': job_id,
            'mapper_spec': '%s.%s.map' % (cls.__module__, cls.__name__),
            'reducer_spec': '%s.%s.reduce' % (cls.__module__, cls.__name__),
            'input_reader_spec': (
                'core.jobs.MultipleDatastoreEntitiesInputReader'),
            'output_writer_spec': (
                'mapreduce.output_writers.BlobstoreRecordsOutputWriter'),
            'mapper_params': {
                MAPPER_PARAM_KEY_ENTITY_KINDS: entity_class_names,
                # Note that all parameters passed to the mapper need to be
                # strings. Also note that the value for this key is determined
                # just before enqueue time, so it will be roughly equal to the
                # actual enqueue time.
                MAPPER_PARAM_KEY_QUEUED_TIME_MSECS: str(
                    utils.get_current_time_in_millisecs()),
            }
        }
        mr_pipeline = MapReduceJobPipeline(
            job_id, '%s.%s' % (cls.__module__, cls.__name__), kwargs)
        mr_pipeline.start(base_path='/mapreduce/worker/pipeline')

    @classmethod
    def _pre_cancel_hook(cls, job_id, cancel_message):
        metadata = cls.get_metadata(job_id)
        if metadata:
            root_pipeline_id = metadata[cls._OUTPUT_KEY_ROOT_PIPELINE_ID]
            pipeline.Pipeline.from_id(root_pipeline_id).abort(cancel_message)

    @classmethod
    def _require_correct_job_type(cls, job_type):
        # Suppress check for correct job type since we cannot pass the specific
        # entity class in the kwargs.
        pass


ABSTRACT_BASE_CLASSES = frozenset([
    BaseJobManager, BaseDeferredJobManager, BaseMapReduceJobManager])


def get_data_for_recent_jobs(recency_msec=DEFAULT_RECENCY_MSEC):
    """Get a list containing data about all jobs.

    This list is arranged in descending order based on the time the job
    was enqueued.

    Args:
    - recency_secs: the threshold for a recent job, in seconds.

    Each element of this list is a dict that represents a job. The dict has the
    following keys:
    - 'id': the job id
    - 'time_started_msec': when the job was started, in milliseconds since the
          epoch
    - 'time_finished_msec': when the job was finished, in milliseconds since
          the epoch
    - 'status_code': the current status of the job
    - 'job_type': the type of this job
    - 'is_cancelable': whether the job can be canceled
    - 'error': any errors pertaining to this job
    """
    recent_job_models = job_models.JobModel.get_recent_jobs(
        recency_msec=recency_msec)
    result = [{
        'id': model.id,
        'time_started_msec': model.time_started_msec,
        'time_finished_msec': model.time_finished_msec,
        'status_code': model.status_code,
        'job_type': model.job_type,
        'is_cancelable': model.is_cancelable,
        'error': model.error,
    } for model in recent_job_models]
    return result


class MultipleDatastoreEntitiesInputReader(input_readers.InputReader):
    _ENTITY_KINDS_PARAM = 'entity_kinds'
    _READER_LIST_PARAM = 'readers'

    def __init__(self, reader_list):
        self._reader_list = reader_list

    def __iter__(self):
        for reader in self._reader_list:
            yield reader

    @classmethod
    def from_json(cls, input_shard_state):
        return cls(input_readers.DatastoreInputReader.from_json(
            input_shard_state[cls._READER_LIST_PARAM]))

    def to_json(self):
        return {
            self._READER_LIST_PARAM: self._reader_list.to_json()
        }

    @classmethod
    def split_input(cls, mapper_spec):
        params = mapper_spec.params
        entity_kinds = params.get(cls._ENTITY_KINDS_PARAM)

        splits = []
        for entity_kind in entity_kinds:
            new_mapper_spec = copy.deepcopy(mapper_spec)
            new_mapper_spec.params['entity_kind'] = entity_kind
            splits.append(
                input_readers.DatastoreInputReader.split_input(
                    new_mapper_spec))

        inputs = []
        for split in splits:
            for item in split:
                inputs.append(MultipleDatastoreEntitiesInputReader(item))
        return inputs

    @classmethod
    def validate(cls, mapper_spec):
        return True  # TODO


class BaseMapReduceJobManagerForContinuousComputations(BaseMapReduceJobManager):

    @classmethod
    def _get_continuous_computation_class(cls):
        """Returns the ContinuousComputationManager class associated with this
        MapReduce job.
        """
        raise NotImplementedError(
            'Subclasses of BaseMapReduceJobManagerForContinuousComputations '
            'must implement the _get_continuous_computation_class() method.')

    @staticmethod
    def _entity_created_before_job_queued(entity):
        """Checks that the given entity was created before the MR job was queued.

        Mapper methods may want to use this as a precomputation check,
        especially if the datastore classes being iterated over are append-only
        event logs.
        """
        created_on_msecs = utils.get_time_in_millisecs(entity.created_on)
        job_queued_msecs = float(context.get().mapreduce_spec.mapper.params[
            MAPPER_PARAM_KEY_QUEUED_TIME_MSECS])
        return job_queued_msecs >= created_on_msecs

    @staticmethod
    def map(item):
        """Implements the map function.  Must be declared @staticmethod."""
        raise NotImplementedError(
            'Classes derived from BaseMapReduceJobManager must implement map '
            'as a @staticmethod.')

    @classmethod
    def _post_completed_hook(cls, job_id):
        cls._get_continuous_computation_class().on_batch_job_completion()

    @classmethod
    def _post_cancel_hook(cls, job_id, cancel_message):
        cls._get_continuous_computation_class().on_batch_job_canceled()

    @classmethod
    def _post_failure_hook(cls, job_id):
        cls._get_continuous_computation_class().on_batch_job_failure()


class BaseContinuousComputationManager(object):
    """This class represents a manager for a continuously-running computation.
    Such computations consist of two parts: a batch job to compute summary
    views, and a realtime layer to augment these batch views with additional
    data that has come in since the last batch job results were computed. The
    realtime layer may provide only approximate results, but the discrepancy
    should be small because the realtime layer is expected to handle a much
    smaller amount of data than the batch layer.

    The batch jobs are run continuously, with each batch job starting
    immediately after the previous run has finished. There are two realtime
    layers that are cleared alternatively after successive batch runs, just
    before a new batch job is enqueued. Events are recorded to all three
    layers.

    Here is a schematic showing how this works. The x-axis represents the
    progression of time. The arrowed intervals in the batch layer indicate how
    long the corresponding batch job takes to run, and the intervals in each
    realtime layer indicate spans between when the data in the realtime layer
    is cleared. Note that a realtime layer is cleared as part of the post-
    processing that happens when a batch job completes, which explains why the
    termination of each batch interval and one of the realtime intervals
    always coincides.

    Batch layer         <----->  <------->  <-------> <-------> <-------->
    Realtime layer 0    <----->  <------------------> <------------------>
    Realtime layer 1    <---------------->  <-----------------> <------ ...
                                 <-- A -->  <-- B -->

    For example, queries arising during the time interval A will use the
    results of the first batch run, plus data from the realtime layer R1.
    Queries arising during the time interval B will use the results of the
    second batch run, plus data from the realtime layer R0.
    """
    # TODO(sll): In the previous docstring, quantify what 'small' means
    # once we have some experience with this running in production.

    @classmethod
    def get_event_types_listened_to(cls):
        """Returns a list of event types that this class subscribes to."""
        raise NotImplementedError(
            'Subclasses of BaseContinuousComputationManager must implement '
            '_get_event_types_listened_to(). This method should return a list '
            'of strings, each representing an event type that this class '
            'subscribes to.')

    @classmethod
    def _get_realtime_datastore_classes(cls):
        """Returns a 2-element list with datastore classes used by the
        realtime layers. These two classes should be subclasses of
        base_models.BaseModel, and should be defined with exactly the
        same attributes and methods so that, regardless of which one is used,
        the logic remains the same. See StartExplorationRealtimeModel0 and
        StartExplorationRealtimeModel1 in core/jobs_test.py for an example
        of how to do this.
        """
        raise NotImplementedError(
            'Subclasses of BaseContinuousComputationManager must implement '
            '_get_realtime_datastore_classes(). This method should return '
            'a 2-element list specifying the datastore classes to be used by '
            'the realtime layers.')

    @classmethod
    def _get_batch_job_manager_class(cls):
        """Returns the manager class for the continuously-running batch job.
        See jobs_test.py for an example of how to do this.
        """
        raise NotImplementedError(
            'Subclasses of BaseContinuousComputationManager must implement '
            '_get_batch_job_manager_class(). This method should return the'
            'manager class for the continuously-running batch job.')

    @classmethod
    def _handle_incoming_event(
            cls, datastore_class, event_type, *args, **kwargs):
        """Records incoming events in the given realtime datastore_class.

        This method should be implemented by subclasses. The args are the
        same as those sent to the event handler corresponding to the event
        type. Note that there may be more than one event type.

        IMPORTANT: This method only gets called as part of the dequeue process
        from a deferred task queue. Developers should expect a delay to occur
        between when the incoming event arrives and when this method is called,
        and should resolve any arguments that depend on local session
        variables (such as the user currently in session) before enqueueing
        this method in the corresponding event handler.

        IMPORTANT: If an exception is raised here, the task queue will retry
        calling it and any mutations made will be redone -- unless the
        exception has type taskqueue_services.PermanentTaskFailure. Developers
        should therefore ensure that _handle_incoming_event() is robust to
        multiple calls for the same incoming event.
        """
        raise NotImplementedError(
            'Subclasses of BaseContinuousComputationManager must implement '
            '_handle_incoming_event(...). Please check the docstring of this '
            'method in jobs.BaseContinuousComputationManager for important '
            'developer information.')

    @classmethod
    def _get_active_realtime_index(cls):
        def _get_active_realtime_index_transactional():
            cc_model = job_models.ContinuousComputationModel.get(
                cls.__name__, strict=False)
            if cc_model is None:
                cc_model = job_models.ContinuousComputationModel(
                    id=cls.__name__)
                cc_model.put()

            return cc_model.active_realtime_layer_index

        return transaction_services.run_in_transaction(
            _get_active_realtime_index_transactional)

    @classmethod
    def _get_active_realtime_datastore_class(cls):
        return cls._get_realtime_datastore_classes()[
            cls._get_active_realtime_index()]

    @classmethod
    def _get_inactive_realtime_datastore_class(cls):
        return cls._get_realtime_datastore_classes()[
            1 - cls._get_active_realtime_index()]

    @classmethod
    def _switch_active_realtime_class(cls):
        def _switch_active_realtime_class_transactional():
            cc_model = job_models.ContinuousComputationModel.get(
                cls.__name__)
            cc_model.active_realtime_layer_index = (
                1 - cc_model.active_realtime_layer_index)
            cc_model.put()
        
        transaction_services.run_in_transaction(
            _switch_active_realtime_class_transactional)

    @classmethod
    def _clear_realtime_datastore_class(
            cls, datastore_class, latest_created_on_datetime):
        """Deletes all entries in the given realtime datastore class whose
        created_on date is before latest_timestamp.
        """
        query = datastore_class.query(
            datastore_class.created_on < latest_created_on_datetime)
        ndb.delete_multi(query.iter(keys_only=True))

    @classmethod
    def _kickoff_batch_job(cls):
        """Create and enqueue a new batch job."""
        if job_models.JobModel.do_unfinished_jobs_exist(cls.__name__):
            logging.error(
                'Tried to start a new batch job of type %s while an existing '
                'job was still running ' % cls.__name__)
            return
        job_manager = cls._get_batch_job_manager_class()
        job_id = job_manager.create_new()
        job_manager.enqueue(job_id)

    @classmethod
    def _register_end_of_batch_job_and_return_status(cls):
        """Processes a 'job finished' event and returns the job's updated status
        code.

        Note that 'finish' in this context might mean 'completed successfully'
        or 'failed'.

        Processing means the following: if the job is currently 'stopping', its
        status is set to 'idle'; otherwise, its status remains as 'running'.
        """
        def _register_end_of_batch_job_transactional():
            """Transactionally change the computation's status when a batch job
            ends."""
            cc_model = job_models.ContinuousComputationModel.get(cls.__name__)
            if (cc_model.status_code ==
                    job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_STOPPING):
                cc_model.status_code = (
                    job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE)
                cc_model.put()

            return cc_model.status_code

        return transaction_services.run_in_transaction(
            _register_end_of_batch_job_transactional)

    @classmethod
    def get_status_code(cls):
        """Returns the status code of the job."""
        return job_models.ContinuousComputationModel.get(
            cls.__name__).status_code

    @classmethod
    def start_computation(cls):
        """(Re)starts the continuous computation corresponding to this class.

        Raises an Exception if the computation is already running.
        """
        def _start_computation_transactional():
            """Transactional implementation for marking a continuous
            computation as started.
            """
            cc_model = job_models.ContinuousComputationModel.get(
                cls.__name__, strict=False)
            if cc_model is None:
                cc_model = job_models.ContinuousComputationModel(
                    id=cls.__name__)

            if (cc_model.status_code != 
                    job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE):
                raise Exception(
                    'Attempted to start computation %s, which is already '
                    'running.' % cls.__name__)

            cc_model.status_code = (
                job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING)
            cc_model.last_started_msec = utils.get_current_time_in_millisecs()
            cc_model.put()

        transaction_services.run_in_transaction(
            _start_computation_transactional)

        # Clear the inactive realtime layer.
        cls._clear_realtime_datastore_class(
            cls._get_inactive_realtime_datastore_class(),
            datetime.datetime.utcnow())

        cls._kickoff_batch_job()

    @classmethod
    def stop_computation(cls, user_id, test_mode=False):
        """Cancels the currently-running batch job.

        No further batch runs will be kicked off.
        """
        # This is not an ancestor query, so it must be run outside a
        # transaction.
        do_unfinished_jobs_exist = (
            job_models.JobModel.do_unfinished_jobs_exist(
                cls._get_batch_job_manager_class().__name__))

        def _stop_computation_transactional():
            """Transactional implementation for marking a continuous
            computation as stopping/idle.
            """
            cc_model = job_models.ContinuousComputationModel.get(cls.__name__)
            # If there is no job currently running, go to IDLE immediately.
            new_status_code = (
                job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_STOPPING if 
                do_unfinished_jobs_exist else
                job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE)
            cc_model.status_code = new_status_code
            cc_model.last_stopped_msec = utils.get_current_time_in_millisecs()
            cc_model.put()

        transaction_services.run_in_transaction(
            _stop_computation_transactional)

        # The cancellation must be done after the continuous computation
        # status update.
        if do_unfinished_jobs_exist:
            unfinished_job_models = job_models.JobModel.get_unfinished_jobs(
                cls._get_batch_job_manager_class().__name__)
            for job_model in unfinished_job_models:
                cls._get_batch_job_manager_class().cancel(
                    job_model.id, user_id)

    @classmethod
    def on_incoming_event(cls, event_type, *args, **kwargs):
        """Handle an incoming event.

        The *args and **kwargs match those passed to the _handle_event() method
        of the corresponding EventHandler subclass.
        """
        for datastore_class in cls._get_realtime_datastore_classes():
            cls._handle_incoming_event(
                datastore_class, event_type, *args, **kwargs)

    @classmethod
    def _process_job_completion_and_return_status(cls):
        """Delete all data in the currently-active realtime_datastore class,
        switch the active class, and return the status.

        This seam was created so that tests would be able to override
        on_batch_job_completion() to avoid kicking off the next job
        immediately.
        """
        cls._clear_realtime_datastore_class(
            cls._get_active_realtime_datastore_class(),
            datetime.datetime.utcnow())
        cls._switch_active_realtime_class()

        def _update_last_finished_time_transactional():
            cc_model = job_models.ContinuousComputationModel.get(cls.__name__)
            cc_model.last_finished_msec = utils.get_current_time_in_millisecs()
            cc_model.put()

        transaction_services.run_in_transaction(
            _update_last_finished_time_transactional)

        return cls._register_end_of_batch_job_and_return_status()

    @classmethod
    def _kickoff_batch_job_after_previous_one_ends(cls):
        """Seam that can be overridden by tests."""
        cls._kickoff_batch_job()

    @classmethod
    def on_batch_job_completion(cls):
        """Called when a batch job completes."""
        job_status = cls._process_job_completion_and_return_status()
        if job_status == job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING:
            cls._kickoff_batch_job_after_previous_one_ends()

    @classmethod
    def on_batch_job_canceled(cls):
        logging.info('Job %s canceled.' % cls.__name__)
        # The job should already be stopping, and should therefore be marked
        # idle.
        job_status = cls._register_end_of_batch_job_and_return_status()
        if job_status != job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE:
            logging.error(
                'Batch job for computation %s canceled but status code not set '
                'to idle.' % cls.__name__)

    @classmethod
    def on_batch_job_failure(cls):
        # TODO(sll): Alert the site admin via email.
        logging.error('Job %s failed.' % cls.__name__)
        job_status = cls._register_end_of_batch_job_and_return_status()
        if job_status == job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING:
            cls._kickoff_batch_job_after_previous_one_ends()


def get_continuous_computations_info(cc_classes):
    """Returns data about the given computations.

    Args:
      cc_classes: a list of subclasses of BaseContinuousComputationManager.

    Returns:
      A list of dicts, each representing a continuous computation. Each dict
      has the following keys:
      - 'computation_type': the type of the computation
      - 'status_code': the current status of the computation
      - 'last_started_msec': when a batch job for the computation was last
            started, in milliseconds since the epoch
      - 'last_finished_msec': when a batch job for the computation last
            finished, in milliseconds since the epoch
      - 'last_stopped_msec': when a batch job for the computation was last
            stopped, in milliseconds since the epoch
      - 'active_realtime_layer_index': the index of the active realtime layer
      - 'is_startable': whether an admin should be allowed to start this
            computation
      - 'is_stoppable': whether an admin should be allowed to stop this
            computation
    """
    cc_models = job_models.ContinuousComputationModel.get_multi(
        [cc_class.__name__ for cc_class in cc_classes])

    result = []
    for ind, model in enumerate(cc_models):
        if model is None:
            cc_dict = {
                'computation_type': cc_classes[ind].__name__,
                'status_code': 'never_started',
                'last_started_msec': None,
                'last_finished_msec': None,
                'last_stopped_msec': None,
                'active_realtime_layer_index': None,
                'is_startable': True,
                'is_stoppable': False,
            }
        else:
            cc_dict = {
                'computation_type': cc_classes[ind].__name__,
                'status_code': model.status_code,
                'last_started_msec': model.last_started_msec,
                'last_finished_msec': model.last_finished_msec,
                'last_stopped_msec': model.last_stopped_msec,
                'active_realtime_layer_index': (
                    model.active_realtime_layer_index),
                # TODO(sll): If a job is stopping, can it be started while it
                # is in the process of stopping?
                'is_startable': model.status_code == (
                    job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_IDLE),
                'is_stoppable': model.status_code == (
                    job_models.CONTINUOUS_COMPUTATION_STATUS_CODE_RUNNING),
            }

        result.append(cc_dict)

    return result