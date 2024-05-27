This directory should contain the following utility-related files:
* `helpers.py` - Miscellaneous helper functions
* `util_config.py` - Common configuration options for all utilities

Each utility should be in its own sub-directory, along with its configuration file, as follows:

/archive
* `archive.py` - Archives free user result files to Glacier
* `archive_config.ini` - Configuration options for archive utility

/notify
* `notify.py` - Sends notification email on completion of annotation job
* `notify_config.ini` - Configuration options for notification utility

/restore
* `restore.py` - Initiates restore of Glacier archive(s)
* `restore_config.ini` - Configuration options for restore utility

/thaw
* `thaw.py` - Saves recently restored archive(s) to S3
* `thaw_config.ini` - Configuration options for thaw utility

If you completed Ex. 14, include your annotator load testing script here
* `ann_load.py` - Annotator load testing script

## Archive Approach
After an annotation job is completed, if the user is a free user, the annotator will send a message to a job archive SQS queue. 
The archive process then:
* Retrieve the file from the S3 bucket using the `s3_key`.
* Upload the result file (not log file) to the Glacier vault.
* Capture the `archive_id` from the Glacier response.
* Update the corresponding DynamoDB item with the `archive_id`.
* Delete the file from the S3 bucket after successfully archiving the file.

## Restoration Approach
* User Upgrade to Premium: When a user upgrades to premium, the web server sends a message to a restore queue implemented using Amazon SQS.
* Restore Process: 
    - The util\restore component receives the message from the restore queue.
    - It submits an expedited retrieval request to restore all archived files of the user from Glacier.
    - If the expedited retrieval request fails, it falls back to using standard retrieval.
* Notification After Retrieval: 
    - Once the retrieval process is completed, a notification is sent to an Amazon SNS topic.
    - The thaw queue, realized by Amazon SQS, subscribes to this SNS topic and receives a message from it.
* Thaw Process:
    - The util\thaw component receives the message from the thaw queue.
    - It downloads the retrieved files from Glacier.
    - It then uploads the files to S3.
* User Access: The user can download the retrieved files from S3 via the GAS web page.