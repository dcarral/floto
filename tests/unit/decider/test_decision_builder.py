import pytest
import json
import datetime
import floto.decider
from floto.specs import ActivityTask, DeciderSpec, Timer
import floto.specs.retry_strategy

@pytest.fixture
def task_1():
    return ActivityTask(name='activity1', version='v1', input={'date':1})

@pytest.fixture
def task_2(task_1):
    return ActivityTask(name='activity2', version='v1', requires=[task_1]) 

@pytest.fixture
def timer():
    return Timer(id_='timer_id', delay_in_seconds=60)

@pytest.fixture
def empty_history(init_response):
    return floto.History(domain='d', task_list='tl', response=init_response)

@pytest.fixture
def dt1():
    return datetime.datetime(2016, 1, 12, hour=1, tzinfo=datetime.timezone.utc)

@pytest.fixture
def builder(task_1, task_2, empty_history):
    execution_graph = floto.decider.ExecutionGraph(activity_tasks=[task_1, task_2])
    b = floto.decider.DecisionBuilder(execution_graph, 'floto_activities')
    b.history = empty_history
    return b

class TestDecisionBuilder(object):
    def test_init(self):
        b = floto.decider.DecisionBuilder('execution_graph', 'atl')
        assert b.execution_graph == 'execution_graph'
        assert b.workflow_fail == False
        assert b.workflow_complete == False
        assert b.activity_task_list == 'atl'

    def test_get_decisions(self, mocker, builder):
        history = type('History', (object,), {'previous_decision_id':0,
                                              'decision_task_started_event_id': 3})
        mocker.patch('floto.decider.DecisionBuilder._collect_decisions', return_value=['d'])
        assert builder.get_decisions(history) == ['d']
        builder._collect_decisions.assert_called_once_with(0,3)

    @pytest.mark.parametrize('failed, completed, is_terminate',
            [(False, False, False),
             (False, True, True),
             (True, False, True),
             (True, True, True)])
    def test_is_terminate_workflow(self, failed, completed, is_terminate, builder):
        builder.workflow_fail = failed
        builder.workflow_complete = completed
        assert builder.is_terminate_workflow() == is_terminate

    def test_collect_decisions_after_workflow_start(self, mocker, builder):
        mocker.patch('floto.decider.DecisionBuilder.get_decisions_after_workflow_start', 
                return_value=['d'])
        decisions = builder._collect_decisions(0, 3)
        assert decisions == ['d']

    def test_collect_decisions_faulty_events(self, builder, mocker):
        events = {'decision_failed':[], 'faulty':['e'], 'completed':[]}
        mocker.patch('floto.History.get_events_for_decision', return_value=events)
        mocker.patch('floto.decider.DecisionBuilder.get_decisions_faulty_tasks', return_value=[])
        builder._collect_decisions(1,2)
        builder.get_decisions_faulty_tasks.assert_called_once_with(['e'])

    def test_collect_decisions_completed_events(self, builder, mocker):
        events = {'decision_failed':[], 'faulty':[], 'completed':['e']}
        mocker.patch('floto.History.get_events_for_decision', return_value=events)
        mocker.patch('floto.decider.DecisionBuilder.get_decisions_after_activity_completion', 
                return_value=[])
        mocker.patch('floto.decider.DecisionBuilder.all_workflow_tasks_finished', 
                return_value=False) 
        builder._collect_decisions(1,2)
        builder.get_decisions_after_activity_completion.assert_called_once_with(['e'])

    def test_collect_decisions_completed_events_workflow_finished(self, builder, mocker):
        events = {'decision_failed':[], 'faulty':[], 'completed':['e']}
        mocker.patch('floto.History.get_events_for_decision', return_value=events)
        fct ='floto.decider.DecisionBuilder.get_workflow_result'
        mocker.patch(fct, return_value='result')
        mocker.patch('floto.decider.DecisionBuilder.all_workflow_tasks_finished', 
                return_value=True) 
        d = builder._collect_decisions(1,2)
        assert builder.is_terminate_workflow() == True
        assert isinstance(d[0], floto.decisions.CompleteWorkflowExecution)
        assert len(d) == 1

    def test_collect_decisions_failed_decision(self, builder, mocker):
        events = {'decision_failed':['e'], 'faulty':[], 'completed':[]}
        mocker.patch('floto.History.get_events_for_decision', return_value=events)
        mocker.patch('floto.decider.DecisionBuilder.get_decisions_decision_failed', 
                return_value=[])
        builder._collect_decisions(1,2)
        builder.get_decisions_decision_failed.assert_called_once_with(['e'])

    def test_get_decisions_first_decision_task(self, builder, empty_history):
        empty_history.first_decision_task = lambda: True
        builder.get_decisions_after_workflow_start = lambda: ['good_decision']
        decisions = builder.get_decisions(empty_history)
        assert decisions == ['good_decision']

    def test_get_decisions_faulty_tasks(self, mocker, task_1, builder, empty_history):
        task_1.retry_strategy = floto.specs.retry_strategy.InstantRetry(retries=1)
        task_1.id_ = 'a_id'
        scheduled_event = {'eventType':'ActivityTaskScheduled',
                           'activityTaskScheduledEventAttributes':{}}
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        mocker.patch('floto.History.get_number_activity_task_failures', return_value=1)
        mocker.patch('floto.History.get_event_task_scheduled', return_value=scheduled_event)
        builder.execution_graph._tasks_by_id = {'a_id':task_1}
        builder.history = empty_history
        d = builder.get_decisions_faulty_tasks(task_events=['te'])
        assert isinstance(d[0], floto.decisions.ScheduleActivityTask)
        assert d[0].activity_id == 'a_id'
        assert builder.is_terminate_workflow() == False

    def test_get_decisions_faulty_tasks_retry_limit_reached(self, mocker, task_1, builder, 
            empty_history, dt1):
        task_1.retry_strategy = floto.specs.retry_strategy.InstantRetry(retries=1)
        task_1.id_ = 'a_id'
        scheduled_event = {'eventType':'ActivityTaskScheduled',
                           'activityTaskScheduledEventAttributes':{}}
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        mocker.patch('floto.History.get_number_activity_task_failures', return_value=2)
        mocker.patch('floto.History.get_event_task_scheduled', return_value=scheduled_event)
        builder.execution_graph._tasks_by_id = {'a_id':task_1}
        builder.history = empty_history
        task_failed_event = {'eventId':4,
                             'eventType':'ActivityTaskFailed',
                             'eventTimestamp':dt1,
                             'activityTaskFailedEventAttributes':{'scheduledEventId':3, 
                                                                  'details':'Error'}}
        d = builder.get_decisions_faulty_tasks(task_events=[task_failed_event])
        assert len(d) == 1
        assert isinstance(d[0], floto.decisions.FailWorkflowExecution)
        assert builder.is_terminate_workflow() == True
        assert d[0].details['a_id'] == 'Error'
        assert d[0].reason == 'task_retry_limit_reached'

    def test_get_decisions_faulty_tasks_without_retry_strategy(self, mocker, task_1, builder, 
            empty_history, dt1):
        task_1.id_ = 'a_id'
        scheduled_event = {'eventType':'ActivityTaskScheduled',
                           'activityTaskScheduledEventAttributes':{}}
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        mocker.patch('floto.History.get_number_activity_task_failures', return_value=2)
        mocker.patch('floto.History.get_event_task_scheduled', return_value=scheduled_event)
        builder.execution_graph._tasks_by_id = {'a_id':task_1}
        builder.history = empty_history
        task_failed_event = {'eventId':4,
                             'eventType':'ActivityTaskFailed',
                             'eventTimestamp':dt1,
                             'activityTaskFailedEventAttributes':{'scheduledEventId':3, 
                                                                  'details':'Error'}}
        d = builder.get_decisions_faulty_tasks(task_events=[task_failed_event])
        assert len(d) == 1
        assert isinstance(d[0], floto.decisions.FailWorkflowExecution)
        assert builder.is_terminate_workflow() == True
        assert d[0].details['a_id'] == 'Error'
        assert d[0].reason == 'task_failed'
    
    def test_get_decisions_faulty_tasks_w_input(self, mocker, task_1, builder, empty_history):
        task_1.retry_strategy = floto.specs.retry_strategy.InstantRetry(retries=1)
        task_1.id_ = 'a_id'
        input_ = json.dumps({'foo':'bar'})
        scheduled_event = {'eventType':'ActivityTaskScheduled',
                           'activityTaskScheduledEventAttributes':{'input':input_}}
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        mocker.patch('floto.History.get_number_activity_task_failures', return_value=1)
        mocker.patch('floto.History.get_event_task_scheduled', return_value=scheduled_event)
        builder.execution_graph._tasks_by_id = {'a_id':task_1}
        builder.history = empty_history
        d = builder.get_decisions_faulty_tasks(task_events=['te'])
        assert d[0].input == {'foo':'bar'} 

    def test_get_decisions_faulty_tasks_early_exit(self, builder):
        builder.workflow_fail = True
        d = builder.get_decisions_faulty_tasks(['e'])
        assert d == []

    def test_get_decisions_after_workflow_start(self, builder, empty_history):
        builder.history = empty_history 
        d = builder.get_decisions_after_workflow_start()
        assert len(d) == 1
        assert isinstance(d[0], floto.decisions.ScheduleActivityTask)
        assert 'activity1:v1' in d[0].activity_id

    def test_get_decision_after_workflow_start_with_timer(self, mocker, builder, timer):
        mocker.patch('floto.decider.ExecutionGraph.get_first_tasks', return_value=[timer])
        d = builder.get_decisions_after_workflow_start()
        assert len(d) == 1
        assert isinstance(d[0], floto.decisions.StartTimer)
        assert d[0].start_to_fire_timeout == 60

    def test_get_decisions_after_workflow_start_w_wf_input(self, builder, init_response, task_1):
        dt1 = datetime.datetime(2016, 1, 12, hour=1, tzinfo=datetime.timezone.utc)
        events = [{'eventId':1,
                   'eventType':'WorkflowExecutionStarted',
                   'eventTimestamp':dt1,
                   'workflowExecutionStartedEventAttributes':{'input':{'foo':'bar'}}}]
        init_response['events'] = events
        builder.history = floto.History(domain='d', task_list='tl', response=init_response)
        d = builder.get_decisions_after_workflow_start()
        assert builder.workflow_input
        assert len(d) == 1
        assert isinstance(d[0], floto.decisions.ScheduleActivityTask)
        assert 'activity1:v1' in d[0].activity_id
        assert d[0].input['activity_task'] == task_1.input 
        assert d[0].input['workflow'] == {'foo':'bar'} 
    
    def test_get_decisions_decision_failed(self, builder, mocker):
        mocker.patch('floto.History.get_id_previous_started', return_value=1)
        mocker.patch('floto.decider.DecisionBuilder._collect_decisions', return_value=['d'])
        failed = {'eventId':3,
                  'eventType':'DecisionTaskTimedOut',
                  'decisionTaskTimedOutEventAttributes':{'startedEventId':2}}
        d = builder.get_decisions_decision_failed([failed])
        assert d == ['d']
        builder._collect_decisions.assert_called_once_with(1,2)
        
    def test_get_decisions_after_successfull_workflow_execution(self, builder, mocker):
        mocker.patch('floto.decider.DecisionBuilder.get_workflow_result', return_value='result')
        d = builder.get_decisions_after_successfull_workflow_execution()
        assert isinstance(d[0], floto.decisions.CompleteWorkflowExecution)
        assert d[0].result == 'result'
        assert builder.is_terminate_workflow() == True

    def test_get_decision_after_activity_completion(self, mocker, builder, empty_history, task_1):
        task_1.id_ = 'a_id'
        task_2 = floto.specs.Timer(id_='t_id', delay_in_seconds=10)
        tasks = [task_1, task_2]
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        mocker.patch('floto.decider.DecisionBuilder.get_tasks_to_be_scheduled', return_value=tasks)
        mocker.patch('floto.decider.DecisionBuilder.get_input_activity_task', return_value={'foo':'bar'})
        builder.history = empty_history
        d = builder.get_decisions_after_activity_completion([{'eventType':'ActivityTaskCompleted'}])
        builder.get_tasks_to_be_scheduled.assert_called_once_with(['a_id'])
        assert len(d) == 2
        assert isinstance(d[0], floto.decisions.ScheduleActivityTask)
        assert d[0].activity_id == 'a_id' 
        assert d[0].input == {'foo':'bar'}
        assert d[1].timer_id == 't_id'

    def test_get_decision_task_with_activity_task(self, mocker, builder):
        task = floto.specs.ActivityTask(name='at', version='v', activity_id='a_id')
        mocker.patch('floto.decider.DecisionBuilder.get_input_activity_task', return_value='i')
        d = builder.get_decision_task(task)
        assert isinstance(d, floto.decisions.ScheduleActivityTask)
        assert d.input == 'i'
        assert d.activity_id == 'a_id'

    def test_get_decision_task_with_timer(self, mocker, builder):
        task = floto.specs.Timer(id_='t_id', delay_in_seconds=10)
        d = builder.get_decision_task(task)
        assert isinstance(d, floto.decisions.StartTimer)
        assert d.start_to_fire_timeout == 10

    def test_get_details_failed_tasks(self, mocker, builder, empty_history):
        mocker.patch('floto.History.get_id_activity_task_event', return_value='a_id')
        task_failed_event = {'eventType':'ActivityTaskFailed',
                             'activityTaskFailedEventAttributes':{'details':'Error'}}
        builder.history = empty_history
        d = builder.get_details_failed_tasks([task_failed_event])
        assert d['a_id'] == 'Error'

    def test_get_input_activity_task(self, builder, task_1):
        i = builder.get_input_activity_task(task_1)
        assert i['activity_task'] == {'date':1}

    def test_get_input_activity_task_depending_task(self, builder, task_1, task_2, mocker, 
            empty_history):
        builder.history = empty_history
        mocker.patch('floto.History.get_result_completed_activity', return_value={'result':'t1'})
        i = builder.get_input_activity_task(task_2)
        assert i[task_1.id_] == {'result':'t1'}

    def test_completed_have_depending_tasks_with_depending(self, mocker, builder, empty_history):
        a = floto.specs.ActivityTask(name='a', version='v', activity_id='a')
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b', requires=[a])
        graph = floto.decider.ExecutionGraph(activity_tasks=[a,b])
        d = floto.decider.DecisionBuilder(graph, 'atl')
        d.history = empty_history
        mocker.patch('floto.History.get_id_task_event', return_value='a')
        assert d.completed_have_depending_tasks(completed_tasks=['a'])


    def test_completed_have_depending_tasks_wo_depending(self, mocker, builder, empty_history):
        a = floto.specs.ActivityTask(name='a', version='v', activity_id='a')
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b', requires=[a])
        graph = floto.decider.ExecutionGraph(activity_tasks=[a,b])
        d = floto.decider.DecisionBuilder(graph, 'atl')
        d.history = empty_history
        mocker.patch('floto.History.get_id_task_event', return_value='b')
        assert not d.completed_have_depending_tasks(completed_tasks=['b'])

    def test_outgoing_vertices_completed(self, mocker, builder, empty_history):
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b')
        builder.history = empty_history
        mocker.patch('floto.decider.ExecutionGraph.outgoing_vertices', return_value=[b])
        mocker.patch('floto.History.is_task_completed', return_value=True)
        assert builder.outgoing_vertices_completed()
        builder.history.is_task_completed.assert_called_once_with(b)

    def test_outgoing_vertices_not_completed(self, mocker, builder, empty_history):
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b')
        builder.history = empty_history
        mocker.patch('floto.decider.ExecutionGraph.outgoing_vertices', return_value=[b])
        mocker.patch('floto.History.is_task_completed', return_value=False)
        assert not builder.outgoing_vertices_completed()
        builder.history.is_task_completed.assert_called_once_with(b)
        
    @pytest.mark.parametrize('desc,assertion', [
        ({'openCounts':{'openActivityTasks':0, 'openTimers':0}}, False),
        ({'openCounts':{'openActivityTasks':1, 'openTimers':0}}, True),
        ({'openCounts':{'openActivityTasks':0, 'openTimers':1}}, True),
        ({'openCounts':{'openActivityTasks':1, 'openTimers':1}}, True)])
    def test_open_task_counts(self, mocker, builder, desc, assertion):
        builder.current_workflow_execution_description = desc
        assert builder.open_task_counts() == assertion

    def test_open_task_counts_wo_current_description(self, builder):
        builder.current_workflow_execution_description = None
        assert not builder.open_task_counts()

    def test_get_task_to_be_scheduled(self, builder):
        a = floto.specs.ActivityTask(name='a', version='v', activity_id='a')
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b')
        c = floto.specs.ActivityTask(name='c', version='v', activity_id='c', requires=[a,b])
        d = floto.specs.ActivityTask(name='d', version='v', activity_id='d', requires=[b])

        graph = floto.decider.ExecutionGraph(activity_tasks=[a,b,c,d])
        builder.execution_graph = graph
        builder.history = empty_history 
        builder.history.is_task_completed = lambda x: {a:True, b:True, c:False, d:False}[x]
        tasks = builder.get_tasks_to_be_scheduled(['a', 'b'])
        assert set([t.id_ for t in tasks]) == set(['c', 'd'])

    def test_get_task_to_be_scheduled_single_id(self):
        t1 = floto.specs.ActivityTask(name='t1', version='v', activity_id='t1')
        t2 = floto.specs.ActivityTask(name='t2', version='v', activity_id='t2', requires=[t1])
        graph = floto.decider.ExecutionGraph(activity_tasks=[t1, t2])
        d = floto.decider.DecisionBuilder(graph, 'atl')
        d.history = empty_history 
        d.history.is_task_completed = lambda x: {t1:True, t2:False}[x]
        tasks = d.get_tasks_to_be_scheduled_single_id('t1')
        assert tasks == [t2]

    def test_get_task_to_be_scheduled_single_id_graph2(self):
        a = floto.specs.ActivityTask(name='a', version='v', activity_id='a')
        b = floto.specs.ActivityTask(name='b', version='v', activity_id='b')
        c = floto.specs.ActivityTask(name='c', version='v', activity_id='c', requires=[a,b])
        d = floto.specs.ActivityTask(name='d', version='v', activity_id='d', requires=[b])

        graph = floto.decider.ExecutionGraph(activity_tasks=[a,b,c,d])
        d = floto.decider.DecisionBuilder(graph, 'atl')
        d.history = empty_history 
        d.history.is_task_completed = lambda x: {a:True, b:True, c:False, d:False}[x]
        assert d.get_tasks_to_be_scheduled_single_id('a') == [c]
        assert [t.id_ for t in d.get_tasks_to_be_scheduled_single_id('b')] == ['c', 'd']

    def test_uniqify_activity_tasks_single_tasks(self, builder):
        t1 = floto.specs.ActivityTask(name='t1', version='v', activity_id='t1')
        tasks = builder.uniqify_activity_tasks([t1])
        assert tasks == [t1]

    def test_uniqify_activity_tasks_two_tasks(self, builder):
        t1 = floto.specs.ActivityTask(name='t1', version='v', activity_id='t1')
        t2 = floto.specs.ActivityTask(name='t2', version='v', activity_id='t2', requires=[t1])
        tasks = builder.uniqify_activity_tasks([t1,t2])
        assert set(tasks) == set([t1, t2])

    def test_uniqify_activity_tasks_equal_tasks(self, builder):
        t1 = floto.specs.ActivityTask(name='t1', version='v', activity_id='t1')
        t2 = floto.specs.ActivityTask(name='t1', version='v', activity_id='t1')
        tasks = builder.uniqify_activity_tasks([t1,t2])
        assert len(tasks) == 1
        assert tasks[0].id_ == 't1'

    def test_get_decision_schedule_activity_task(self, builder):
        at = floto.specs.ActivityTask(name='at_name', version='at_version', activity_id='at_id')
        d = builder.get_decision_schedule_activity_task(at)
        assert isinstance(d, floto.decisions.ScheduleActivityTask)
        assert d.activity_type.name == at.name
        assert d.activity_type.name == at.name
        assert d.activity_id == at.id_
        assert d.task_list == builder.activity_task_list

    def test_get_decision_start_timer(self, builder):
        timer_task = floto.specs.Timer(id_='t_id', delay_in_seconds=60)
        decision = builder.get_decision_start_timer(timer_task)
        assert decision.start_to_fire_timeout == 60
        assert decision.timer_id == 't_id'

    def test_get_workflow_result(self, builder, empty_history, mocker, task_2):
        mocker.patch('floto.History.get_result_completed_activity', return_value={'foo':'bar'})
        builder.history = empty_history
        assert builder.get_workflow_result()[task_2.id_] == {'foo':'bar'}

    def test_get_workflow_result_wo_result(self, builder, empty_history, mocker):
        mocker.patch('floto.History.get_result_completed_activity', return_value=None)
        builder.history = empty_history
        assert builder.get_workflow_result() == None

    def test_all_workflow_tasks_finished_depending_tasks(self, builder, mocker):
        mocker.patch('floto.decider.DecisionBuilder.completed_have_depending_tasks', 
                return_value=True)
        assert not builder.all_workflow_tasks_finished(['t'])

    def test_all_workflow_tasks_finished_open_tasks(self, builder, mocker):
        mocker.patch('floto.decider.DecisionBuilder.completed_have_depending_tasks', 
                return_value=False)
        mocker.patch('floto.decider.DecisionBuilder.open_task_counts', 
                return_value=True)
        assert not builder.all_workflow_tasks_finished(['t'])

    def test_all_workflow_tasks_finished_not_completed(self, builder, mocker):
        mocker.patch('floto.decider.DecisionBuilder.completed_have_depending_tasks', 
                return_value=False)
        mocker.patch('floto.decider.DecisionBuilder.open_task_counts', 
                return_value=False)
        mocker.patch('floto.decider.DecisionBuilder.outgoing_vertices_completed', 
                return_value=False)
        assert not builder.all_workflow_tasks_finished(['t'])

    def test_all_workflow_tasks_finished(self, builder, mocker):
        mocker.patch('floto.decider.DecisionBuilder.completed_have_depending_tasks', 
                return_value=False)
        mocker.patch('floto.decider.DecisionBuilder.open_task_counts', 
                return_value=False)
        mocker.patch('floto.decider.DecisionBuilder.outgoing_vertices_completed',
                return_value=True)
        assert builder.all_workflow_tasks_finished(['t'])






