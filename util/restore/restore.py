# restore.py
#
# NOTE: This file lives on the Utils instance
#
# Copyright (C) 2011-2019 Vas Vasiliadis
# University of Chicago
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import os
import sys

# Import utility helpers
sys.path.insert(1, os.path.realpath(os.path.pardir))
import helpers

# Get configuration
from configparser import SafeConfigParser
config = SafeConfigParser(os.environ)
config.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'restore_config.ini'))

import boto3, json
from botocore import exceptions

# Add utility code here
def restore():
    sqs = boto3.client('sqs',region_name=config['aws']['AwsRegionName'])
    while True:
        response = sqs.receive_message(
            QueueUrl=config['aws']['SQSJobRestoreUrl'], 
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10
            )
        try:  # Receive message
            message = response['Messages'][0]
            message_body = json.loads(message["Body"])
            receipt_handle = message['ReceiptHandle']
        except KeyError: # No messages in queue
            continue
        user_id = message_body['user_id']
        _, _, _, _, role, _, _ = helpers.get_user_profile(id = user_id)
        if role == 'premium_user':
            # Retrieve the archive id
            ids = get_archive_ids(user_id)
            for id in ids:
                job_id = id['job_id']
                archive_id = id['results_file_archive_id']
                restore_file(job_id, archive_id)
        else:
            pass
        #Delete the message
        sqs.delete_message(
                QueueUrl=config['aws']['SQSJobRestoreUrl'],
                ReceiptHandle=receipt_handle
            )

def get_archive_ids(user_id):
    dynamodb = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])
    table = dynamodb.Table(config['aws']['DynamoAnnotationsTable']) 
    try:
        response = table.query(
            IndexName = config['aws']['DynamoAnnotationsTableIndex'],
            Select='SPECIFIC_ATTRIBUTES',
            ProjectionExpression="results_file_archive_id,job_id",
            KeyConditionExpression="user_id = :val",
            ExpressionAttributeValues={
                ":val": user_id},
            FilterExpression='attribute_not_exists(s3_key_result_file) and attribute_exists(results_file_archive_id)'
        )
        if 'Items' not in response:
            return []
        if len(response['Items']) == 0:
            return []
        return response['Items']
    except exceptions.ClientError as e: 
        print(e)
        return []

def restore_file(job_id, archive_id):
    # Add restore message using job_id
    dynamodb = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])
    table = dynamodb.Table(config['aws']['DynamoAnnotationsTable']) 
    table.update_item(
            Key = { 'job_id': job_id },
            UpdateExpression="SET restore_message = :val",
            ExpressionAttributeValues={':val': "The file is being restored, please wait."}     
        )
    # Lauch the retrieval job
    glacier = boto3.client('glacier',region_name=config['aws']['AwsRegionName'])
    # Use the Expedited mode first.
    try:
        glacier.initiate_job(
            vaultName=config['aws']['GlacierVaultName'],
            jobParameters={
                'Type': 'archive-retrieval',
                'ArchiveId': archive_id, 
                'SNSTopic': config['aws']['SNSThawRequestArn'], 
                'Tier': "Expedited"
            }
        )
    # Expedited mode failed, use the standard mode.
    except glacier.exceptions.InsufficientCapacityException: 
        glacier.initiate_job(
            vaultName=config['aws']['GlacierVaultName'],
            jobParameters={
                'Type': 'archive-retrieval',
                'ArchiveId': archive_id, 
                'SNSTopic': config['aws']['SNSThawRequestArn'], 
                'Tier': "Standard"
            }
        )
    
if __name__ == "__main__":
    restore()
### EOF


'''References:
1.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/initiate_job.html
2.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/update_item.html
3.https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
'''