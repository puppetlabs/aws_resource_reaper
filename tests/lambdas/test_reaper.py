# Standard library imports
from unittest.mock import patch
from unittest.mock import MagicMock

# Reaper import
import lambdas.ec2.reaper as reaper 

@patch.object(reaper, 'os')
def test_determine_live_mode(mock_os):
    mock_os.environ = {'LIVEMODE': 'true'}
    assert reaper.determine_live_mode() == True

    mock_os.environ = {'NO_LIVE_MODE': 'true'}
    assert reaper.determine_live_mode() == False

    mock_os.environ = {'LIVE_MODE': 'false'}
    assert reaper.determine_live_mode() == False

def test_validate_lifetime_value():
    assert reaper.validate_lifetime_value('indefinite') == ('indefinite')
    assert reaper.validate_lifetime_value('5m') == (5, 'm')
    assert reaper.validate_lifetime_value('2h') == (2, 'h')
    assert reaper.validate_lifetime_value('2d') == (2, 'd')
    assert reaper.validate_lifetime_value('2w') == (2, 'w')
    assert reaper.validate_lifetime_value('42w') == (42, 'w')
    assert reaper.validate_lifetime_value('2t') is None

def test_calculate_lifetime_delta():
    minute = reaper.validate_lifetime_value('1m')
    delta = reaper.calculate_lifetime_delta(minute)
    assert delta.total_seconds() == 60

    hour = reaper.validate_lifetime_value('1h')
    delta = reaper.calculate_lifetime_delta(hour)
    assert delta.total_seconds() == 3600

    day = reaper.validate_lifetime_value('1d')
    delta = reaper.calculate_lifetime_delta(day)
    assert delta.total_seconds() == 86400

    week = reaper.validate_lifetime_value('1w')
    delta = reaper.calculate_lifetime_delta(week)
    assert delta.total_seconds() == 604800

def test_get_tag():
    ec2_mock = MagicMock()
    ec2_mock.tags = None
    assert reaper.get_tag(ec2_mock, 'some_tag') is None

    ec2_mock.tags = [{'Key': 'no_match_', 'Value': 'no_match_value'}]
    assert reaper.get_tag(ec2_mock, 'some_tag') is None

    ec2_mock.tags = [{'Key': 'match', 'Value': 'match_value'}]
    assert reaper.get_tag(ec2_mock, 'match') is 'match_value'

def test_terminate_instance():
    with patch.object(reaper, 'LIVEMODE') as mock_live_mode:

        mock_live_mode.return_value = True
        ec2_mock = MagicMock()
        reaper.terminate_instance(ec2_mock, 'test terminate')
        ec2_mock.terminate.assert_called_with()

        mock_live_mode.return_value = False
        ec2_mock2 = MagicMock()
        reaper.terminate_instance(ec2_mock, 'test terminate')
        ec2_mock2.terminate.assert_not_called()

def test_stop_instance():
    with patch.object(reaper, 'LIVEMODE') as mock_live_mode:

        mock_live_mode.return_value = True
        ec2_mock = MagicMock()
        reaper.stop_instance(ec2_mock, 'test stop')
        ec2_mock.stop.assert_called_with()

        mock_live_mode.return_value = False
        ec2_mock2 = MagicMock()
        reaper.stop_instance(ec2_mock, 'test stop')
        ec2_mock2.stop.assert_not_called()

@patch.object(reaper, 'get_tag')
@patch.object(reaper, 'terminate_instance')
def test_validate_ec2_termination_date(mock_terminate_instance, mock_get_tag):
    ec2_mock = MagicMock()

    mock_get_tag.return_value = reaper.timenow_with_utc().isoformat()
    reaper.validate_ec2_termination_date(ec2_mock)
    mock_terminate_instance.assert_called_with(ec2_mock, 'The termination_date has passed')

    mock_terminate_instance.reset_mock()
    mock_get_tag.return_value = (reaper.timenow_with_utc() + reaper.datetime.timedelta(hours=1)).isoformat()
    reaper.validate_ec2_termination_date(ec2_mock)
    mock_terminate_instance.assert_not_called()

@patch.object(reaper, 'calculate_lifetime_delta')
@patch.object(reaper, 'get_tag')
@patch.object(reaper, 'LIVEMODE')
def test_wait_for_tags(mock_live_mode, mock_get_tag, mock_calculate_lifetime_delta):
    # When elapsed time to wait is 0, assert terminate is called
    mock_ec2_instance = MagicMock()
    mock_live_mode.return_value = True
    reaper.wait_for_tags(mock_ec2_instance, 0)
    mock_ec2_instance.terminate.assert_called_with()


    with patch.object(reaper, 'validate_lifetime_value') as mock_validate_lifetime_value:
        # When the time is in the future, assert terminate is not called
        mock_ec2_instance.reset_mock()
        mock_validate_lifetime_value.return_value = 2, 'w'
        # We use side_effect to mock the initial get_tag call for 'termination_date',
        # return a valid lifetime tag, and then return True for the next get_tag call for
        # the termination_date
        mock_get_tag.side_effect = [None, '2w', True]
        reaper.wait_for_tags(mock_ec2_instance, 1)
        mock_ec2_instance.terminate.assert_not_called()
        mock_ec2_instance.create_tags.assert_called()

        mock_ec2_instance.reset_mock()
        # We use side_effect to mock the initial get_tag call for 'termination_date', and
        # then return True for the next get_tag call for 'lifetime'
        mock_get_tag.side_effect = [None, True]
        mock_validate_lifetime_value.return_value = None
        reaper.wait_for_tags(mock_ec2_instance, 1)
        mock_ec2_instance.terminate.assert_called_with()

@patch.object(reaper, 'ec2')
@patch.object(reaper, 'wait_for_tags')
@patch.object(reaper, 'validate_ec2_termination_date')
def test_enforce(mock_validate_ec2_termination_date, mock_wait_for_tags, mock_ec2):
    event = {'detail': {'instance-id': 'test_instance_id' }}
    reaper.enforce(event, 'context')
    mock_ec2.Instance.assert_called_with(id=event['detail']['instance-id'])
    mock_wait_for_tags.assert_called()
    mock_validate_ec2_termination_date.assert_called()

@patch.object(reaper, 'get_tag')
@patch.object(reaper, 'LIVEMODE')
@patch.object(reaper, 'ec2')
def test_terminate_expired_instances(mock_ec2, mock_live_mode, mock_get_tag):
    mock_get_tag.return_value = reaper.timenow_with_utc().isoformat()
    mock_ec2_instance = MagicMock()
    mock_ec2.instances.filter.return_value = [mock_ec2_instance]
    mock_live_mode.return_value = True
    reaper.terminate_expired_instances('event', 'context')
    mock_ec2_instance.terminate.assert_called_with()

    # ensure that the reaper does not terminate instances with a valid future
    # termination_date
    mock_get_tag.return_value = (reaper.timenow_with_utc() + reaper.datetime.timedelta(hours=1)).isoformat()
    mock_ec2_instance.reset_mock()
    reaper.terminate_expired_instances('event', 'context')
    mock_ec2_instance.terminate.assert_not_called()

    #ensure that the reaper does not terminate instances with valid
    #indefinite tag for termination_date
    indefinite = 'indefinite'
    mock_get_tag.return_value = indefinite
    mock_ec2_instance.reset_mock()
    reaper.terminate_expired_instances('event', 'context')
    mock_ec2_instance.terminate.assert_not_called()

    #ensure that reaper stops instances with missing
    #tag for termination_date
    none_tag = None
    mock_get_tag.return_value = none_tag
    mock_ec2_instance.reset_mock()
    reaper.terminate_expired_instances('event', 'context')
    mock_ec2_instance.stop.assert_called()

    #ensure that reaper stops instances with
    #incorrect tag for termination_date
    incorrect_tag = '3/7/2018'
    mock_get_tag.return_value = incorrect_tag
    mock_ec2_instance.reset_mock()
    reaper.terminate_expired_instances('event', 'context')
    mock_ec2_instance.stop.assert_called()
