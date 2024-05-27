# Copyright (C) 2011-2019 Vas Vasiliadis
# University of Chicago
#
# Wrapper script for running AnnTools
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'

import sys
sys.path.insert(1, './anntools')
import driver
sys.path.append("../util")
import helpers

import time, boto3, logging, os, shutil, json
from botocore.exceptions import ClientError
from configparser import SafeConfigParser
from boto3.dynamodb.conditions import Key

config = SafeConfigParser(os.environ)
config.read(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'ann_config.ini'))

"""A rudimentary timer for coarse-grained profiling
"""
class Timer(object):
  def __init__(self, verbose=True):
    self.verbose = verbose

  def __enter__(self):
    self.start = time.time()
    return self

  def __exit__(self, *args):
    self.end = time.time()
    self.secs = self.end - self.start
    if self.verbose:
      print(f"Approximate runtime: {self.secs:.2f} seconds")

if __name__ == '__main__':
	# Call the AnnTools pipeline
  if len(sys.argv) > 1:
    with Timer():
      filename = sys.argv[1]
      driver.run(filename, 'vcf')
 
      # Upload the results and log files to S3 results bucket
      job_id = sys.argv[2]
      user_email = sys.argv[3]

      # Connect to S3 and Dynamo DB
      S3 = boto3.client('s3', region_name=config['aws']['AwsRegionName'])
      Dynamo = boto3.resource('dynamodb',region_name=config['aws']['AwsRegionName'])

      # Retrieve job detail from the Dynamo table
      table = Dynamo.Table(config['aws']['DynamoAnnotationsTable'])
      response = table.get_item(Key = {'job_id': job_id})
      S3_input_file = response["Item"]["s3_key_input_file"]
      annotFile = S3_input_file[:-4]+config['aws']['ResultFilePostfix']
      logFile = S3_input_file+config['aws']['LogFilePostfix']

      # Upload result file and log file
      try:
        S3.upload_file(filename[:-4]+config['aws']['ResultFilePostfix'],config['aws']['AWSS3ResultBucket'],annotFile)
      except ClientError as e:
        logging.error(e)
      try:
        S3.upload_file(filename+config['aws']['LogFilePostfix'],config['aws']['AWSS3ResultBucket'],logFile)
      except ClientError as e:
        logging.error(e)

      # Delete local job files
      job_dir = filename[0 : filename.find("/",9)]
      try:
        shutil.rmtree(job_dir)
      except:
        print("Failed to delete local job directory for job id", job_id)

      # Update the job item in my DynamoDB table 
      response  = table.update_item(
        Key = { 'job_id': job_id },
        UpdateExpression = 'SET s3_results_bucket = :val1, \
          s3_key_result_file = :val2, \
          s3_key_log_file = :val3, \
          complete_time = :val4, \
          job_status = :val5',
        ExpressionAttributeValues = {
                ':val1': config['aws']['AWSS3ResultBucket'],
                ':val2': annotFile,
                ':val3': logFile,
                ':val4': int(time.time()),
                ':val5': "COMPLETED"
        }
      )
      
      # Publish a message to notify the user that the job is completed
      sqs = boto3.client('sqs',region_name=config['aws']['AwsRegionName'])
      sqs.send_message(
                QueueUrl=config['aws']['SQSJobResultsUrl'],
                MessageBody=json.dumps({
                  "job_id": job_id, 
                  "user_email": user_email,
                  "job_status": "COMPLETED"
                })
        )
      # Pulish a job archive request if the user is fee_user
      resp = table.query(KeyConditionExpression=Key('job_id').eq(job_id))
      user_id = resp['Items'][0]['user_id']
      _, _, _, _, role, _, _ = helpers.get_user_profile(id=user_id)
      if role == "free_user":
        sqs.send_message(
                QueueUrl=config['aws']['SQSJobArchivesUrl'],
                DelaySeconds = 300, # Delay 5 mins for user to free-download.
                MessageBody=json.dumps({
                    "user_id": user_id, 
                    "job_id": job_id, 
                    "s3_key_result_file": annotFile})
        )
      else:
        pass
  
      
  else:
    print("A valid .vcf file must be provided as input to this program.")

### EOF
'''Reference: 
1.https://favtutor.com/blogs/merge-dictionaries-python
2.https://stackoverflow.com/questions/63626527/dynamodb-update-using-boto3'''