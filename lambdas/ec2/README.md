# AWS EC2 Reaper 

This AWS EC2 Reaper works using tags set on the instance itself. The Reaper is 
composed of two AWS Lambdas: the "Schema Enforcer" and the "Terminator". The 
Schema Enforcer ensures that all new instances have been correctly tagged, and
the Terminator runs periodically to terminate all running instances past their
termination date.

## Rules and Usage

**TL;DR** Tag an instance with a `lifetime` tag on creation.  A valid `lifetime` 
tag is a string of an integer value with a 1 letter unit of w(weeks), d(days), 
h(hours). For example, `1w` is 1 week, `2d` is 2 days, and `3h` is 3 hours. 

1. The Schema Enforcer ensures that a newly created EC2 instance has a valid 
future date set for termination. The Schema Enforcer looks for a `lifetime` tag
to determine that date. A valid `lifetime` tag is a string of an integer 
value with a 1 letter unit of w(weeks), d(days), h(hours). For example, `1w` is 
1 week, `2d` is 2 days, and `3h` is 3 hours. The Schema Enforcer will calculcate 
a future date based upon the `lifetime` tag and set a new `termination_date` tag 
on that instance.
    * Instead of setting the `lifetime` tag, you can set a `termination_date` 
    tag directly to specify the date the instance expires. A `termination_date` 
    must be a valid IS0 8601 value with a UTC offset defined.
2. If there is an error determining the future termination date, the instance is 
terminated. If 4 minutes elapse and no future termination date has been 
determined, the instance is terminated.
3. The Terminator runs periodically to ensure that all EC2 instances are
terminated if they are past their `termination_date`. If an instance needs its 
lifetime extended beyond its original future terminatation date, the 
`termination_date` tag should be updated directly.
 
## Implementation and Details
The following sections are details meant for people implementing the the AWS
EC2 Reaper.

### Installation
Currently, there is no build script yet for the Reaper; you will need to copy 
the `reaper.py` file to both AWS Lambdas. The Schema Enforcer AWS Lambda 
should call the `enforce` method from the `reaper.py` file, and the Terminator 
AWS Lambda should call the `terminate_expired_instances` method. You will also
need to ensure that there is a role for the AWS Lambda with sufficient privilege
and access to read events and delete instances. Once those are in place, you 
will need to add a rule in Cloudwatch for the Schema Enforcer with this event 
pattern:

```json
{
  "source": [
    "aws.ec2"
  ],
  "detail-type": [
    "EC2 Instance State-change Notification"
  ],
  "detail": {
    "state": [
      "pending"
    ]
  }
}
```

The Terminator should run on a Cloudwatch schedule, configurable in the [AWS EC2
GUI](http://docs.aws.amazon.com/AmazonCloudWatch/latest/events/ScheduledEvents.html). 

To turn these AWS Lambdas on and allow them to actually terminate instances, 
they must run in an environment where `LIVE_MODE` is defined as true. All other
values, including undefined, result in `LIVE_MODE` evaluating to false and
no actual termination of EC2 instances.

### Components

#### Schema Enforcer
The Schema Enforcer is an AWS Lambda that is designed to be triggered when an EC2 
instance enters the 'pending' state. This AWS Lambda waits for an EC2 instance to 
have a valid `termination_date` tag associated with it. This AWS Lambda also 
listens for a `lifetime` tag; if found, it calculates a new future date and adds 
that date as the `termination_date` for the instance.

The Schema Enforcer terminates instances that do not have valid tags, or if the 
timeout period MINUTES_TO_WAIT has elapsed. Unhandled errors are raised, but the 
Schema Enforcer does not terminate the instance in these cases. The Schema 
Enforcer does not terminate instances after the schema has been enforced; the 
Terminator is responsible for that.

#### Terminator
The Terminator is a simple AWS Lambda that looks for a `termination_date` tag on
an instance and terminates it if it is past its `termination_date`. If the 
`termination_date` is missing or malformed, the script logs those instances in its
output. This AWS Lambda is designed to be run periodically; depending on 
your needs, every 15 minutes should be more than sufficient. The Terminator does
not ensure that EC2 instances have valid tags; the Schema Enforcer is responsible 
for that.

The `termination_date` must be in a IS0-8601 format with a UTC offset.

#### Hipchat Notifier
The Hipchat Notifier is a separate Lambda that can run and post data about 
terminated instances; it runs in its own Lambda, tied to the output of both the
Schema Enforcer and Terminator looking for a "REAPER TERMINATION" string match in
the output of the either Lambda. A Cloudwatch Log trigger with a filter pattern
or `REAPER TERMINATION` should be attached to this Lambda, and the hipchat room
and hipchat auth token should be set as environment variables.
