import subprocess, boto3, shutil, json, os
from botocore.exceptions import ClientError
from pathlib import Path
from boto3.dynamodb.conditions import Attr

from configparser import SafeConfigParser
config = SafeConfigParser(os.environ)
config.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'ann_config.ini'))

def annotator():
    # Connect to SQS, dynamo DB, and S3
    sqs = boto3.client('sqs', region_name=config['aws']['AwsRegionName'])
    dynamo = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])
    table = dynamo.Table(config['aws']['DynamoAnnotationsTable'])
    s3 = boto3.resource('s3', region_name=config['aws']['AwsRegionName'])

    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/receive_message.html
    # Poll the message queue in a loop
    while True:
        # Attempt to read a message from the queue using long polling
        receive_response = sqs.receive_message(
            QueueUrl=config['aws']['SQSJobRequestUrl'],
            WaitTimeSeconds=int(config['aws']['SQSWaitTimeSeconds']),
            MaxNumberOfMessages=1
        )
        # If receive message successfully, process the jobs,
        # otherwise continue with the next loop.
        if "Messages" not in receive_response:
            continue
        # Process job.
        message = receive_response["Messages"][0]
        message_handle = message["ReceiptHandle"] # used to delete the file
        body = json.loads(json.loads(message["Body"])["Message"])

        # Extract job parameters from the message
        job_id = body["job_id"]
        S3_input_file = body["s3_key_input_file"]
        input_bucket = body["s3_inputs_bucket"]
        file_name = body["input_file_name"]
        user_email = body['user_email']

        # Copy the input file from S3 to a local directory
        Path("./"+job_id).mkdir(parents = True, exist_ok = True)
        s3.meta.client.download_file(input_bucket, S3_input_file,"./"+ job_id + "/" + file_name)
        
        # Launch annotation job as a background process.
        try:
            subprocess.Popen(["python", "./run.py", "./" + job_id + "/" + file_name, job_id, user_email])
        except:
            print ("Failed to launch the annotator job for job id", job_id)
            # Delete the directory we just created.
            try:
                shutil.rmtree("./" + job_id)
            except:
                print("Failed to delete the local files for job id", job_id)

        # https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/update_item.html
        # Update the job status to RUNNING
        try:
            table.update_item(
                Key = { 'job_id': job_id },
                UpdateExpression="set job_status = :newJobStatus",
                ExpressionAttributeValues={':newJobStatus': 'RUNNING'},
                ConditionExpression=Attr("job_status").eq("PENDING")
            )
        except ClientError as e:  
            # If the status is already changed to COMPLETE, ignore update.
            if e.response['Error']['Code']=='ConditionalCheckFailedException':
                pass
            else:
                print("Annotator updating database failed.")
                return
            
        # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/delete_message.html
        # Delete the message from the queue
        sqs.delete_message(
                QueueUrl=config['aws']['SQSJobRequestUrl'],
                ReceiptHandle=message_handle
            )
            
if __name__ == "__main__":
    annotator()

'''Reference:
1. https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/receive_message.html
2. https://stackoverflow.com/questions/34600003/converting-json-to-string-in-python
3. https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/delete_message.html
4. https://stackoverflow.com/questions/34447304/example-of-update-item-in-dynamodb-boto3
5. https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/update_item.html
'''