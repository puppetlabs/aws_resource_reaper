#!/usr/bin/env python
import boto3
import json
import ast
import zlib
import base64
import os
from urllib.request import Request, urlopen

RED_ALERTS = [
    'The following instances have been stopped due to unparsable or missing termination_date tags:'
    ]

NO_ALERT = [
    'REAPER TERMINATION completed. The following instances have been deleted due to expired termination_date tags: [].',
    'REAPER TERMINATION completed. The following instances have been stopped due to unparsable or missing termination_date tags: [].',
    'REAPER TERMINATION completed. The following instances have been deleted due to expired termination_date tags: []. The following instances have been stopped due to unparsable or missing termination_date tags: [].'
    'REAPER TERMINATION completed. LIVEMODE is off, would have stopped the following instances due to unparsable or missing termination_date tags: []',
    'REAPER TERMINATION completed. LIVEMODE is off, would have deleted the following instances: []',
    'REAPER TERMINATION completed. LIVEMODE is off, would have deleted the following instances: []. REAPER would have stopped the following instances due to unparsable or missing termination_date tags: []'
    ]

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

def read_webhook():
    """
    Read in the environment SLACK_WEBHOOK.
    """
    return os.environ['SLACKWEBHOOK']

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
    event_dict = ast.literal_eval(unzipped_string.decode('utf-8'))
    return event_dict

def post(event, context):
    """
    :param event: AWS Log Event.
    :param context: Object to determine runtime info of the Lambda function.

    See http://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html for more info
    on context.
    Process an AWS Log event and post it to a Slack channel via workflow.
    """

    WEBHOOK = read_webhook()

    event_processed = process_subscription_notification(event)

    for log_event in event_processed['logEvents']:

        message = log_event['message']
        for entry in NO_ALERT:
            if entry in message:
                return "Success"
        headers = {
            "content-type": "application/json"}
        datastr = json.dumps({
            "account": get_account_alias(),
            "message": message,
            "region": determine_region()
        })
        datastr = datastr.encode('utf-8')
        request = Request(WEBHOOK, headers=headers, data=datastr)
        request.add_header('Content-Length', len(datastr))
        uopen = urlopen(request)
        uopen.close()
        assert uopen.code == 200
    return "Success"
