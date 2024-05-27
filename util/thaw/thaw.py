# thaw.py
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
import helpers, boto3, json
from botocore import exceptions

# Get configuration
from configparser import SafeConfigParser
config = SafeConfigParser(os.environ)
config.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'thaw_config.ini'))

# Add utility code here
def thaw():
    while True:
        # Receive the message for retrieval completion 
        sqs = boto3.client('sqs', region_name=config['aws']['AwsRegionName'])
        receive_response = sqs.receive_message(
            QueueUrl=config['aws']['SQSThawRequestUrl'], 
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20
        )
        try:
            message = receive_response['Messages'][0]
            message_body = json.loads(json.loads(message["Body"])['Message'])
            receipt_handle = message['ReceiptHandle'] # Used to delete the message
        except KeyError:
            # No messages in queue, continue to the next loop.
            continue
        if message_body['StatusCode'] != 'Succeeded': 
            print('Archive retrieval error - StatusCode was not <Succeeded>')
            continue
        jobId = message_body['JobId']
        archive_id = message_body['ArchiveId']
        # Download the result file
        glacier = boto3.client('glacier',region_name=config['aws']['AwsRegionName'])
        download_response = glacier.get_job_output(
            vaultName=config['aws']['GlacierVaultName'],
            jobId=jobId
        )
        result_file_body = download_response['body'].read()
        # Upload the result file to S3
        dynamodb = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])
        table = dynamodb.Table(config['aws']['DynamoAnnotationsTable']) 
        try:
            query_response = table.query(
                IndexName = config['aws']['DynamoAnnotationsTableIndex'],
                Select='SPECIFIC_ATTRIBUTES',
                ProjectionExpression="job_id,s3_key_input_file",
                KeyConditionExpression="results_file_archive_id = :val",
                ExpressionAttributeValues={
                    ":val": archive_id},
            )
        except:
            print("Error query the DynamoDB!")
            return
        if ('Items' not in query_response) or (len(query_response['Items']) == 0):
            print("Item not found in DynamoDB!")
            sqs.delete_message(
                QueueUrl=config['aws']['SQSThawRequestUrl'],
                ReceiptHandle=receipt_handle
            )
            glacier.delete_archive(
                vaultName=config['aws']['GlacierVaultName'],
                archiveId=archive_id
            )
            continue
        # Generate key result file name
        item = query_response['Items'][0]
        job_id = item['job_id']
        s3_key_input_file = item['s3_key_input_file']
        s3_key_result_file = s3_key_input_file[:-4]+config['aws']['ResultFilePostfix']
        S3 = boto3.client('s3',region_name=config['aws']['AwsRegionName'])
        # Upload file
        try:
            put_response = S3.put_object(
                Body=result_file_body,
                Bucket=config['aws']['AWSS3ResultBucket'],
                Key=s3_key_result_file
            )
        except exceptions.S3UploadFailedError as e:
            print('Server Error: ' + f'{e}')
            return
        # Update dynamo db for this item
        try:
            update_response = table.update_item(
                Key={'job_id': job_id},
                ExpressionAttributeValues={
                    ':val': s3_key_result_file
                }, 
                UpdateExpression='SET s3_key_result_file = :val REMOVE results_file_archive_id, restore_message'
            )
        except exceptions.ClientError as e:
            print('Server Error: ' + f'{e}')
            return
        # Delete file from Glacier
        delete_archive_response = glacier.delete_archive(
                vaultName=config['aws']['GlacierVaultName'],
                archiveId=archive_id
            )

if __name__ == "__main__":
    thaw()
### EOF

''' References:
1.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/receive_message.html
2.https://stackoverflow.com/questions/15283281/amazon-glacier-how-to-associate-an-archive-retrieval-sns-response-with-its-job
3.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/get_job_output.html
4.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
5.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/update_item.html
6.https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
7.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/glacier/client/delete_archive.html
'''
