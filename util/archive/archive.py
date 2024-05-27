# archive.py
#
# NOTE: This file lives on the Utils instance
#
# Copyright (C) 2011-2019 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import os
import sys
import boto3, json, logging

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

# Get configuration
from configparser import SafeConfigParser
config = SafeConfigParser(os.environ)
config.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'archive_config.ini'))

from botocore import exceptions
# Add utility code here
def archive():
    S3 = boto3.client('s3',region_name=config['aws']['AwsRegionName'])
    dynamodb = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])
    table = dynamodb.Table(config['aws']['DynamoAnnotationsTable'])
    sqs = boto3.client('sqs',region_name=config['aws']['AwsRegionName'])
    glacier = boto3.client('glacier',region_name=config['aws']['AwsRegionName'])
    while True: 
        # Proceed the message one at a time.
        response = sqs.receive_message(
            QueueUrl=config['aws']['SQSJobArchivesUrl'], 
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10
        )
        try:
            message = response['Messages'][0]
            message_body = json.loads(message["Body"])
            receipt_handle = message['ReceiptHandle'] # Used to delete the message
        except KeyError:
            # No messages in queue, continue to the next loop.
            continue
        # Parse the message body.
        user_id = message_body['user_id']
        job_id = message_body['job_id']
        s3_key_result_file = message_body['s3_key_result_file']
        _, _, _, _, role, _, _ = helpers.get_user_profile(id=user_id)
        # User has upgraded to premium, don't archive but delete the message.
        if role == "premium_user":
            sqs.delete_message(
                QueueUrl=config['aws']['SQSJobArchivesUrl'],
                ReceiptHandle=receipt_handle
                )
            continue
        # Get result file from S3
        try:
            response = S3.get_object(
                Bucket=config['aws']['S3ResultsBucket'],
                Key=s3_key_result_file
                )
            obj = response['Body'].read()
        except: 
            # Others have already archived this file.
            delete_message(receipt_handle)
            continue
        # Archive the file to Glacier
        resp = glacier.upload_archive(
                    vaultName = config['aws']['GlacierVaultName'],
                    body = obj
                )
        archive_id = resp['archiveId']
        # Update the Dynamo DB with archive id
        try:
            table.update_item(
            Key = { 'job_id': job_id },
                        UpdateExpression="SET results_file_archive_id = :val1 REMOVE s3_key_result_file",
                        ExpressionAttributeValues={':val1': archive_id},
                        
                    )
        except Exception as e:
            logging.error(e)
            return None
        # Delete the object from S3
        try:
            S3.delete_object(
                Bucket=config['aws']['S3ResultsBucket'],
                Key=s3_key_result_file
                )
        except: 
            pass
        # Delete the message
        delete_message(receipt_handle)

def delete_message(receipt_handle):
    sqs = boto3.client('sqs',region_name=config['aws']['AwsRegionName'])
    sqs.delete_message(
                QueueUrl=config['aws']['SQSJobArchivesUrl'],
                ReceiptHandle=receipt_handle
            )
    return

if __name__ == '__main__':
    archive()
### EOF


'''References:
1.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/upload_archive.html
2.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/update_item.html
3.https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
4.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/delete_object.html
'''