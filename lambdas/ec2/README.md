# AWS EC2 Reaper 

This AWS EC2 Reaper works using tags set on the instance itself. The Reaper is composed of two AWS Lambdas: the "Schema Enforcer" and the "Terminator". The Schema Enforcer ensures that all new instances have a valid `termination_date` tag set when the EC2 resource is created, and the Terminator runs periodically to delete all instances past their `termination_date`.

## Rules and Usage

1. The Schema Enforcer ensures that newly created EC2 instances have a valid `termination_date` tag set. If a valid `termination_date` is not set within 4 minutes, the Schema Enforcer will terminate the instance and raise an exception. The `termination_date` must be a valid IS0 8601 value with a UTC offset defined.
    * Instead of setting the `termination_date` directly, you can set a `lifetime` tag to specify how long the instance should live. A valid value is a string of of an integer value with a 1 letter unit of w(weeks), d(days), h(hours). For example, `1w` is 1 week, `2d` is 2 days, and `3h` is 3 hours. The Schema Enforcer will caculcate a future date based upon the `lifetime` tag and set a new `termination_date` on the instance.
2. The Terminator runs periodically to ensure that all EC2 instances are terminated if they are past their 'termination_date'. If the lifetime for an instance needs to be prolonged, the `termination_date` should be updated directly.

## Installation 
Currently, there is no build script yet for the Reaper; you will need to copy the `schema_enforcer.py` and `terminator.py` files to Lambdas in AWS. You will also need to ensure that there is a role for the lambda's with sufficient privilege and access to read events and delete instances. Once those are in place, you will need to add a rule in Cloudwatch for the Enforcer with this event pattern:

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

The Terminator should run on a Cloudwatch schedule, configurable in the AWS EC2 GUI. 

Both Lambdas do not actually delete instances unless they are running in an environment where the `LIVE_MODE` environment variable is defined. To turn these Lambdas on and allow them to actually terminate instances, they must run in an environment where `LIVE_MODE` is defined. Please note that defining `LIVE_MODE` to `False` does not turn off the Lambdas; the environment variable must be removed entirely to turn the Reaper back to a non-destructive mode.

## Components

### Schema Enforcer
The Schema Enforcer is a lambda that is designed to be triggered when an EC2 instance enters the 'pending' state. This lambda waits for an EC2 instance to have a valid `termination_date` tag associated with it. This Lambda also listens for a `lifetime` tag; if found, it calculates a new future date and adds that date as the `termination_date` for the instance. The Schema Enforcer terminates instances that do no have valid `termination_date` tags after 4 minutes, and reports that termination as an exception raised. The Schema Enforcer does not terminate instances after the schema has been enforced; the Terminator is responsible for that.

### Terminator
The Terminator is a simple lambda that looks for a `termination_date` tag on an instance and terminates it if it is past its `termination date`. If the `termination_date` is missing or malformed, the script reports those instances in an exception. This lambda is designed to be run periodically; depending on your needs, every 15 minutes should be more than sufficient. The Terminator does not ensure that EC2 instances have valid tags; the Schema Enforcer is responsible for that.

The `termination_date` must be in a IS0-8601 format with a UTC offset.