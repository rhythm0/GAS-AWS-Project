# views.py
#
# Copyright (C) 2011-2020 Vas Vasiliadis
# University of Chicago
#
# Application logic for the GAS
#
##
__author__ = 'Vas Vasiliadis <vas@uchicago.edu>'



import uuid
import time
import json
import os
from datetime import datetime

import boto3
import logging
from boto3.dynamodb.conditions import Key
from botocore.client import Config
from botocore.exceptions import ClientError

from flask import (abort, flash, redirect, render_template,
  request, session, url_for)

from gas import app, db
from decorators import authenticated, is_premium
from auth import get_profile, update_profile
from time import strftime, localtime


"""Start annotation request
Create the required AWS S3 policy document and render a form for
uploading an annotation input file using the policy document.

Note: You are welcome to use this code instead of your own
but you can replace the code below with your own if you prefer.
"""
@app.route('/annotate', methods=['GET'])
@authenticated
def annotate():
  # Create a session client to the S3 service
  s3 = boto3.client('s3',
    region_name=app.config['AWS_REGION_NAME'],
    config=Config(signature_version='s3v4'))

  bucket_name = app.config['AWS_S3_INPUTS_BUCKET']
  user_id = session['primary_identity']

  # Generate unique ID to be used as S3 key (name)
  key_name = app.config['AWS_S3_KEY_PREFIX'] + user_id + '/' + \
    str(uuid.uuid4()) + '~${filename}'

  # Create the redirect URL
  redirect_url = str(request.url) + '/job'

  # Define policy fields/conditions
  encryption = app.config['AWS_S3_ENCRYPTION']
  acl = app.config['AWS_S3_ACL']
  fields = {
    "success_action_redirect": redirect_url,
    "x-amz-server-side-encryption": encryption,
    "acl": acl
  }
  conditions = [
    ["starts-with", "$success_action_redirect", redirect_url],
    {"x-amz-server-side-encryption": encryption},
    {"acl": acl}
  ]

  # Generate the presigned POST call
  try:
    presigned_post = s3.generate_presigned_post(
      Bucket=bucket_name, 
      Key=key_name,
      Fields=fields,
      Conditions=conditions,
      ExpiresIn=app.config['AWS_SIGNED_REQUEST_EXPIRATION'])
  except ClientError as e:
    app.logger.error(f"Unable to generate presigned URL for upload: {e}")
    return abort(500)
    
  # Render the upload form which will parse/submit the presigned POST
  return render_template('annotate.html', s3_post=presigned_post)


"""Fires off an annotation job
Accepts the S3 redirect GET request, parses it to extract 
required info, saves a job item to the database, and then
publishes a notification for the annotator service.

Note: Update/replace the code below with your own from previous
homework assignments
"""
@app.route('/annotate/job', methods=['GET'])
@authenticated
def create_annotation_job_request():
  # Get bucket name, key, and job ID from the S3 redirect URL
  bucket_name = str(request.args.get('bucket'))
  s3_key = str(request.args.get('key'))
  user_id = session['primary_identity']
  
  # Extract the job ID from the S3 key
  first_slash = s3_key.find("/") + 1
  job_idStart = s3_key.find("/",first_slash)+1
  job_idEnd = s3_key.find("~", job_idStart)
  job_id = s3_key[job_idStart:job_idEnd]
  file_name = s3_key[s3_key.find("~")+1:]

  # Persist job to database
  dynamo = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  table = dynamo.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])

  item = {
      "job_id": job_id,
      "user_id": user_id,
      "input_file_name": file_name,
      "s3_inputs_bucket": bucket_name,
      "s3_key_input_file": s3_key,
      "submit_time": int(time.time()),
      "job_status": "PENDING"
  }
  table.put_item(Item = item)

  #Include user's email for job completion notification
  item['user_email']=session['email']

  # Send message to request queue
  client = boto3.client('sns', region_name=app.config['AWS_REGION_NAME'])
  client.publish(
    TopicArn = app.config["AWS_SNS_JOB_REQUEST_TOPIC"],
    Message = json.dumps(item),
    MessageStructure = 'string'
  )

  return render_template('annotate_confirm.html', job_id=job_id)


"""List all annotations for the user
"""
@app.route('/annotations', methods=['GET'])
@authenticated
def annotations_list():
  annotations_list = []
  # Get list of annotations to display
  dynamo = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  table = dynamo.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])
  resp = table.query(
    IndexName="user_id_index",
    KeyConditionExpression=Key('user_id').eq(session['primary_identity']))
  for item in resp['Items']:
    item['submit_time'] = strftime('%Y-%m-%d %H:%M', localtime(item['submit_time']))
    annotations_list.append(item)
  
  return render_template('annotations.html', annotations=annotations_list)


"""Display details of a specific annotation job
"""
@app.route('/annotations/<id>', methods=['GET'])
@authenticated
def annotation_details(id):
  # Connect to S3
  S3 = boto3.client('s3',region_name=app.config['AWS_REGION_NAME'])
  free_access_expired = False
  # Query item using job id
  dynamo = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  table = dynamo.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])
  item = table.query(KeyConditionExpression=Key('job_id').eq(str(id)))
  # Check if item exists
  if "Items" not in item:
    app.logger.error('Job does not exist.')
    return abort(500)
  if len(item['Items']) == 0:
    app.logger.error('Job does not exist.')
    return abort(500)
  # Job exists, since id is UUID, there is guaranteed to be only one item, retrieve it.
  item = item["Items"][0]
  # Check if job belongs to the user
  if item['user_id'] != session['primary_identity']:
    app.logger.error('Not authorized to view this job.')
    return abort(500)
  # Generate presigned url to download input file
  try:
    input_file_url = S3.generate_presigned_url('get_object',
                                                Params={'Bucket': item['s3_inputs_bucket'],
                                                        'Key': item['s3_key_input_file']},
                                                )
  except ClientError as e:
    logging.error(e)
    return None
  item['input_file_url'] = input_file_url
  # Convert epoch time to human readable time
  item['submit_time'] = strftime('%Y-%m-%d %H:%M', localtime(item['submit_time']))
  # If job completed, check if user is eligible to download result file
  if item['job_status'] == "COMPLETED":
    if (session.get('role') == "free_user") and (time.time() - float(item['complete_time']) > 300):
      free_access_expired = True
    elif (session.get('role') == "premium_user") and ('s3_key_result_file' not in item):
      # Give a restore message if not exists
      if "restore_message" not in item:
        item['restore_message'] = "The file is being restored, please wait."
    # Generate presigned url to download result file expiring in 5 minutes
    else:
      try:
        result_file_url = S3.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': item['s3_results_bucket'],
                                    'Key': item['s3_key_result_file']},
                            ExpiresIn=app.config['AWS_SIGNED_DOWNLOAD_EXPIRATION']
                          )
      except ClientError as e:
        logging.error(e)
        return None
      item['result_file_url'] = result_file_url
    # Convert complete time to human readable
    item['complete_time'] = strftime('%Y-%m-%d %H:%M', localtime(item['complete_time']))
    
  
  return render_template('annotation_details.html', annotation=item, free_access_expired=free_access_expired)


"""Display the log file contents for an annotation job
"""
@app.route('/annotations/<id>/log', methods=['GET'])
@authenticated
def annotation_log(id):
  S3 = boto3.client('s3', region_name=app.config['AWS_REGION_NAME'])
  dynamo = boto3.resource('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  table = dynamo.Table(app.config["AWS_DYNAMODB_ANNOTATIONS_TABLE"])
  item = table.query(KeyConditionExpression=Key('job_id').eq(str(id)))["Items"][0]
  try: 
    response = S3.get_object(
      Bucket=item['s3_results_bucket'],
      Key=item['s3_key_log_file']
    )
  except ClientError as e:
    logging.error(e)
    return None
  content = response['Body'].read().decode("utf-8")
  return render_template('view_log.html', job_id=id, log_file_contents=content)


"""Subscription management handler
"""
@app.route('/subscribe', methods=['GET', 'POST'])
@authenticated
def subscribe():
  if (request.method == 'GET'):
    # Display form to get subscriber credit card info
    if (session.get('role') == "free_user"):
      return render_template('subscribe.html')
    else:
      return redirect(url_for('profile'))

  elif (request.method == 'POST'):
    # Update user role to allow access to paid features
    update_profile(
      identity_id=session['primary_identity'],
      role="premium_user"
    )

    # Update role in the session
    session['role'] = "premium_user"

    # Request restoration of the user's data from Glacier
    # Add code here to initiate restoration of archived user data
    # Make sure you handle files not yet archived!
    sqs = boto3.client('sqs', region_name=app.config['AWS_REGION_NAME'])
    # Send the restore message for the user to SQS restore queue
    sqs.send_message(
      QueueUrl=app.config['AWS_SQS_JOB_RESTORE_QUEUE_URL'],
      DelaySeconds = 10,
      MessageBody=json.dumps({"user_id": session['primary_identity']})
    )

    # Display confirmation page
    return render_template('subscribe_confirm.html') 

"""Reset subscription
"""
@app.route('/unsubscribe', methods=['GET'])
@authenticated
def unsubscribe():
  # Hacky way to reset the user's role to a free user; simplifies testing
  update_profile(
    identity_id=session['primary_identity'],
    role="free_user"
  )
  # Upload free user's files to glacier by publishing a message
  '''dynamodb = boto3.client('dynamodb', region_name=app.config['AWS_REGION_NAME'])
  sqs = boto3.client('sqs', region_name=app.config['AWS_REGION_NAME'])
  try:
      response = dynamodb.query(
          TableName=app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE'],
          IndexName = app.config['AWS_DYNAMODB_ANNOTATIONS_TABLE_INDEX_NAME'],
          KeyConditionExpression="user_id = :val",
          ExpressionAttributeValues={
              ":val": session['primary_identity']},
          FilterExpression='attribute_exists(s3_key_result_file) and attribute_not_exists(results_file_archive_id)'
      )
      for item in response['Items']:
        if time.time()-item['complete_time'] > 300:
          sqs.send_message(
                QueueUrl=app.config['AWS_SQS_JOB_ARCHIVE_QUEUE_URL'],
                MessageBody=str({
                    'user_id': item['user_id'], 
                    'job_id': item['job_id'], 
                    's3_key_result_file': item['s3_key_result_file']})
            )
  except:
    pass'''
  return redirect(url_for('profile'))


"""DO NOT CHANGE CODE BELOW THIS LINE
*******************************************************************************
"""

"""Home page
"""
@app.route('/', methods=['GET'])
def home():
  return render_template('home.html')

"""Login page; send user to Globus Auth
"""
@app.route('/login', methods=['GET'])
def login():
  app.logger.info(f"Login attempted from IP {request.remote_addr}")
  # If user requested a specific page, save it session for redirect after auth
  if (request.args.get('next')):
    session['next'] = request.args.get('next')
  return redirect(url_for('authcallback'))

"""404 error handler
"""
@app.errorhandler(404)
def page_not_found(e):
  return render_template('error.html', 
    title='Page not found', alert_level='warning',
    message="The page you tried to reach does not exist. \
      Please check the URL and try again."
    ), 404

"""403 error handler
"""
@app.errorhandler(403)
def forbidden(e):
  return render_template('error.html',
    title='Not authorized', alert_level='danger',
    message="You are not authorized to access this page. \
      If you think you deserve to be granted access, please contact the \
      supreme leader of the mutating genome revolutionary party."
    ), 403

"""405 error handler
"""
@app.errorhandler(405)
def not_allowed(e):
  return render_template('error.html',
    title='Not allowed', alert_level='warning',
    message="You attempted an operation that's not allowed; \
      get your act together, hacker!"
    ), 405

"""500 error handler
"""
@app.errorhandler(500)
def internal_error(error):
  return render_template('error.html',
    title='Server error', alert_level='danger',
    message="The server encountered an error and could \
      not process your request."
    ), 500

### EOF
'''Reference:
1.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb/client/query.html
2.https://github.com/aws-samples/aws-dynamodb-examples/blob/master/DynamoDB-SDK-Examples/python/WorkingWithQueries/query_equals.py
3.https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-presigned-urls.html
4.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/get_object.html
5.https://stackoverflow.com/questions/36205481/read-file-content-from-s3-bucket-with-boto3
6.https://stackoverflow.com/questions/35758924/how-do-we-query-on-a-secondary-index-of-dynamodb-using-boto3
7.https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/send_message.html'''