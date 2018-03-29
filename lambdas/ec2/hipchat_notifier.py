#!/usr/bin/env python
import boto3
import json
import ast
import zlib
import base64
import os
from urllib2 import Request, urlopen

RED_ALERTS = [
    'REAPER TERMINATION completed but bad tags found'
    ]

NO_ALERT = 'REAPER TERMINATION completed. The following instances have been deleted: []'

def get_account_alias():
    """
    Return the first alias listed from Amazon. Return generic Reaper if unable
    to find account alias.
    """
    client = boto3.client('iam')
    try:
        return client.list_account_aliases()['AccountAliases'][0]
    except Exception:
        print('Unable to find account alias')
        return 'AWS EC2 Reaper'

def read_token():
    """
    Read in the environment HIPCHAT_TOKEN.
    """
    return os.environ['HIPCHATTOKEN']

def read_room_id():
    """
    Read in the environment HIPCHAT_ROOM_ID.
    """
    return int(os.environ['HIPCHATROOMID'])

def determine_region():
    """
    Determine the current region execution
    """
    region = boto3.session.Session().region_name
    return region

def process_subscription_notification(event):
    """
    param: event: AWS log event.

    Decompresses the data from an AWS Log Event and returns a standard dict.
    """
    zipped = base64.standard_b64decode(event['awslogs']['data'])
    unzipped_string = zlib.decompress(zipped, 16+zlib.MAX_WBITS)
    event_dict = ast.literal_eval(unzipped_string)
    return event_dict

def is_red_alert(message):
    for alert in RED_ALERTS:
        if alert in message:
            return True

def determine_hipchat_color(event, message):
    """
    :param event: the decompressed AWS Log event.
    :param message: string of the message to send to hipchat.

    If the message is considered an alarm, a 'red alert', set the hipchat color
    to red; otherwise, set terminator messages to purple, and set the enforcer
    messages to the yellow.
    """
    if is_red_alert(message):
        return 'red'
    elif 'terminator' in event['logGroup']:
        return 'purple'
    else:
        return 'yellow' #the default color of hipchat notifications

def post(event, context):
    """
    :param event: AWS Log Event.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    Process an AWS Log event and post it to a Hipchat Room.
    """

    V2TOKEN = read_token()
    ROOMID = read_room_id()
    url = 'https://api.hipchat.com/v2/room/%d/notification' % ROOMID

    event_processed = process_subscription_notification(event)

    for log_event in event_processed['logEvents']:

        message = log_event['message']
        if NO_ALERT in message
            return "Success"
        
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer %s" % V2TOKEN}
        datastr = json.dumps({
            'message': message,
            'color': determine_hipchat_color(event_processed, message),
            'message_format': 'html',
            'notify': False,
            'from': get_account_alias() + " " + determine_region()})
        request = Request(url, headers=headers, data=datastr)
        uopen = urlopen(request)
        rawresponse = ''.join(uopen)
        uopen.close()
        assert uopen.code == 204
    return "Success"
