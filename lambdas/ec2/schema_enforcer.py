from __future__ import print_function

import json
import boto3
import datetime
import time
import dateutil
import re

ec2 = boto3.resource('ec2')

def check_for_tag(ec2_instance, tag_name):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.
    :param tag_name: A string of the key name you are searching for.

    This method returns False if the ec2 instance currently has no tags
    or if the tag is not found. If the tag is found, it returns the tag
    value.
    """
    ec2_instance.load()
    if ec2_instance.tags is None:
        return False
    for tag in ec2_instance.tags:
        if tag['Key'] == tag_name:
            return tag['Value']
        else:
            continue
    return False

def timenow_with_utc():
    """
    Return a datetime object that includes the tzinfo for utc time.
    """
    time = datetime.datetime.utcnow()
    time = time.replace(tzinfo=dateutil.tz.tz.tzutc())
    return time

def wait_for_tags(ec2_instance, wait_time):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance
    :param wait_time: The number of minutes to wait for the 'termination_date'

    This method exits when either a termination date is found or the wait_time
    has passed. The method looks for the 'lifetime' key, parses it, and sets
    the 'termination_date' on the instance.
    """
    start = timenow_with_utc()
    timeout = start + datetime.timedelta(minutes=wait_time)

    while timenow_with_utc() < timeout:
        if type(check_for_tag(ec2_instance, 'termination_date')) is str:
            print("'termination_date' tag found!")
            break
        if check_for_tag(ec2_instance, 'lifetime') is False:
            print("No 'lifetime' tag found; sleeping for 15s")
            time.sleep(15)
            continue
        print('lifetime tag found')
        lifetime = check_for_tag(ec2_instance,'lifetime')
        lifetime_delta = calculate_lifetime_delta(lifetime)
        termination_date = start + lifetime_delta
        ec2_instance.create_tags(
                Tags=[
                    {
                        'Key': 'termination_date',
                        'Value': termination_date.isoformat()
                    }
                ]
            )
    else:
        print('No termination_date found; terminating instance')
        # TODO: This code should also eventually report terminated instances
        # to some log somewhere.
        # ec2_instance.terminate()
        raise Exception('EC2 instance deleted because no termination_date found within 4 minutes of creation.')


def validate_ec2_termination_date(ec2_instance):
    """
    :param ec2_instance: a boto3 resource representing an Amazon EC2 Instance.

    Validates that an ec2 instance has a termination_date in the future.
    """
    ec2_termination_date = check_for_tag(ec2_instance, 'termination_date')
    if ec2_termination_date is False:
        print("No termination_date found; deleting ec2 instance and raising Exception.")
        # TODO: Remove this code whence the reaper goes live
        # ec2_instance.delete()
        raise ValueError('No termination_date found; ec2 instance deleted.')
    try:
        dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
    except Exception as e:
        if e is TypeError:
            if re.search(r'(offset-naive).+(offset-aware)', e.__str__):
                print('The termination_date requires a UTC offset; terminating instance')
            else:
                print('Unable to parse the termination_date; deleting ec2 instance and raising original exception.')
        # TODO: Remove this comment whence the reaper goes live
        # ec2_instance.delete()
        raise

    if dateutil.parser.parse(ec2_termination_date) > timenow_with_utc():
        ttl = dateutil.parser.parse(ec2_termination_date) - timenow_with_utc()
        print("EC2 instance will be terminated {0} seconds from now, roughly".format(ttl.seconds))
    else:
        print("The termination_date has passed; deleting ec2 resource and raising exception")
        # TODO: This code should also eventually report terminated instances
        # to some log somewhere.
        # ec2_instance.terminate()
        raise Exception("The EC2 instance has been deleted because the termination_date has passed.")



def calculate_lifetime_delta(lifetime_value):
    """
    :param lifetime_value: string that with an integer and a single letter unit of (w)eeks, (d)ays, or (h)ours.

    Takes a string of of an integer value with a 1 letter unit of w(weeks), d(days), h(hours).
    """
    regex = r'^([0-9]+)(w|d|h)$'
    match = re.search(regex, lifetime_value)
    if match is None:
        print("Invalid lifetime syntax; please provide valid integer followed by 1 letter unit: 1w=1week, 2d=2day, 3h=3hours.")
        # TODO: Remove this comment whence the reaper goes live
        # ec2_instance.delete()
        raise ValueError("The lifetime value {0} cannot be parsed".format(lifetime_value))

    toople = match.groups()
    unit = toople[1]
    length = int(toople[0])
    if unit == 'w':
        return datetime.timedelta(weeks=length)
    elif unit =='h':
        return datetime.timedelta(hours=length)
    elif unit == 'd':
        return datetime.timedelta(days=length)
    else:
        raise ValueError("Unable to parse the unit '{0}'".format(unit))


def lambda_handler(event, context):
    print(event)
    print(event['detail']['instance-id'])
    instance = ec2.Instance(id=event['detail']['instance-id'])
    try:
        wait_for_tags(instance, 4)
        validate_ec2_termination_date(instance)
    except Exception as e:
        print("Error in schema enforcement; it is likely the ec2 instance {0} has been deleted as a result.".format(instance.id))
        # TODO: add in code to alert somebody exception happened
        raise
