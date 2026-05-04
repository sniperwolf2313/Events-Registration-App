import csv
import io
import json
import os
import traceback
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]
NOTIFICATIONS_QUEUE_URL = os.environ["NOTIFICATIONS_QUEUE_URL"]

REPORTS_PREFIX = os.environ.get("REPORTS_PREFIX", "reports")
PRESIGNED_URL_EXPIRES_SECONDS = int(os.environ.get("PRESIGNED_URL_EXPIRES_SECONDS", "86400"))

events_table = dynamodb.Table(EVENTS_TABLE)
registrations_table = dynamodb.Table(REGISTRATIONS_TABLE)
reports_table = dynamodb.Table(REPORTS_TABLE)

REPORT_STATUS_PROCESSING = "PROCESSING"
REPORT_STATUS_COMPLETED = "COMPLETED"
REPORT_STATUS_FAILED = "FAILED"


def utc_now():
    return datetime.now(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def decimal_to_native(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)

    if isinstance(value, list):
        return [decimal_to_native(item) for item in value]

    if isinstance(value, dict):
        return {key: decimal_to_native(item) for key, item in value.items()}

    return value


def parse_json(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        if not value.strip():
            return {}
        return json.loads(value)

    return {}


def parse_sqs_record(record):
    body = parse_json(record.get("body"))

    if isinstance(body.get("Message"), str):
        return json.loads(body["Message"])

    return body


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def query_registrations(event_id):
    items = []
    last_key = None

    while True:
        params = {
            "KeyConditionExpression": Key("eventId").eq(event_id)
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        result = registrations_table.query(**params)

        items.extend([
            decimal_to_native(item)
            for item in result.get("Items", [])
        ])

        last_key = result.get("LastEvaluatedKey")

        if not last_key:
            break

    return items


def update_report(report_id, status, values=None):
    values = values or {}
    values["status"] = status
    values["updatedAt"] = iso_z(utc_now())

    expression_names = {}
    expression_values = {}
    parts = []

    for index, (key, value) in enumerate(values.items()):
        name_placeholder = f"#f{index}"
        value_placeholder = f":v{index}"

        expression_names[name_placeholder] = key
        expression_values[value_placeholder] = value
        parts.append(f"{name_placeholder} = {value_placeholder}")

    reports_table.update_item(
        Key={
            "reportId": report_id
        },
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values
    )


def build_csv(event_item, registrations, report_metadata):
    output = io.StringIO()
    writer = csv.writer(output)

    registered = [
        item for item in registrations
        if item.get("status") == "REGISTERED"
    ]

    cancelled = [
        item for item in registrations
        if item.get("status") == "CANCELLED"
    ]

    writer.writerow(["Report"])
    writer.writerow(["reportId", report_metadata["reportId"]])
    writer.writerow(["reportType", report_metadata["reportType"]])
    writer.writerow(["generatedAt", iso_z(utc_now())])
    writer.writerow([])

    writer.writerow(["Event"])
    writer.writerow(["eventId", event_item.get("eventId", "")])
    writer.writerow(["title", event_item.get("title", "")])
    writer.writerow(["location", event_item.get("location", "")])
    writer.writerow(["startDate", event_item.get("startDate", "")])
    writer.writerow(["endDate", event_item.get("endDate", "")])
    writer.writerow(["status", event_item.get("status", "")])
    writer.writerow(["capacity", event_item.get("capacity", 0)])
    writer.writerow(["availableSlots", event_item.get("availableSlots", 0)])
    writer.writerow([])

    writer.writerow(["Summary"])
    writer.writerow(["totalRegistrations", len(registrations)])
    writer.writerow(["totalRegistered", len(registered)])
    writer.writerow(["totalCancelled", len(cancelled)])
    writer.writerow([])

    writer.writerow([
        "userId",
        "fullName",
        "email",
        "registrationDate",
        "status"
    ])

    for item in sorted(registrations, key=lambda row: row.get("registrationDate", "")):
        writer.writerow([
            item.get("userId", ""),
            item.get("fullName", ""),
            item.get("email", ""),
            item.get("registrationDate", ""),
            item.get("status", "")
        ])

    return output.getvalue(), {
        "totalRegistrations": len(registrations),
        "totalRegistered": len(registered),
        "totalCancelled": len(cancelled)
    }


def upload_report(report_id, csv_content):
    date_prefix = utc_now().strftime("%Y/%m/%d")
    key = f"{REPORTS_PREFIX}/{date_prefix}/{report_id}.csv"

    s3.put_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
        Body=csv_content.encode("utf-8-sig"),
        ContentType="text/csv; charset=utf-8",
        Metadata={
            "report-id": report_id
        }
    )

    return key


def generate_download_url(bucket, key):
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": bucket,
            "Key": key
        },
        ExpiresIn=PRESIGNED_URL_EXPIRES_SECONDS
    )


def enqueue_report_ready_notification(report_data):
    message = {
        "type": "REPORT_READY",
        "channel": "EMAIL",
        "templateType": "REPORT_READY",
        "to": report_data["requestedByEmail"],
        "templateData": {
            "reportId": report_data["reportId"],
            "eventId": report_data["eventId"],
            "downloadUrl": report_data["downloadUrl"],
            "expiresInSeconds": PRESIGNED_URL_EXPIRES_SECONDS,
            "totalRegistrations": report_data["summary"]["totalRegistrations"],
            "totalRegistered": report_data["summary"]["totalRegistered"],
            "totalCancelled": report_data["summary"]["totalCancelled"]
        }
    }

    params = {
        "QueueUrl": NOTIFICATIONS_QUEUE_URL,
        "MessageBody": json.dumps(message, ensure_ascii=False)
    }

    if NOTIFICATIONS_QUEUE_URL.endswith(".fifo"):
        params["MessageGroupId"] = f"report-{report_data['reportId']}"
        params["MessageDeduplicationId"] = f"REPORT_READY-{report_data['reportId']}"

    result = sqs.send_message(**params)

    return {
        "messageId": result.get("MessageId")
    }


def process_report_request(payload):
    report_id = payload["reportId"]
    event_id = payload["eventId"]

    update_report(
        report_id=report_id,
        status=REPORT_STATUS_PROCESSING
    )

    event_item = get_event(event_id)

    if not event_item:
        raise ValueError(f"Event not found: {event_id}")

    registrations = query_registrations(event_id)

    csv_content, summary = build_csv(
        event_item=event_item,
        registrations=registrations,
        report_metadata=payload
    )

    s3_key = upload_report(
        report_id=report_id,
        csv_content=csv_content
    )

    download_url = generate_download_url(
        bucket=REPORTS_BUCKET,
        key=s3_key
    )

    notification_result = enqueue_report_ready_notification({
        "reportId": report_id,
        "eventId": event_id,
        "requestedByEmail": payload["requestedByEmail"],
        "downloadUrl": download_url,
        "summary": summary
    })

    update_report(
        report_id=report_id,
        status=REPORT_STATUS_COMPLETED,
        values={
            "s3Bucket": REPORTS_BUCKET,
            "s3Key": s3_key,
            "downloadUrlExpiresInSeconds": PRESIGNED_URL_EXPIRES_SECONDS,
            "summary": summary
        }
    )

    return {
        "reportId": report_id,
        "eventId": event_id,
        "s3Bucket": REPORTS_BUCKET,
        "s3Key": s3_key,
        "summary": summary,
        "notification": notification_result
    }


def mark_report_failed(payload, error_message):
    report_id = payload.get("reportId")

    if not report_id:
        return

    update_report(
        report_id=report_id,
        status=REPORT_STATUS_FAILED,
        values={
            "failedAt": iso_z(utc_now()),
            "errorMessage": error_message
        }
    )


def handler(event, context):
    batch_item_failures = []
    results = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")

        try:
            payload = parse_sqs_record(record)

            print("Processing report request:")
            print(json.dumps(payload, ensure_ascii=False))

            result = process_report_request(payload)

            print("Report generated:")
            print(json.dumps(result, ensure_ascii=False))

            results.append(result)

        except Exception as exc:
            print("Error generating report:")
            print(str(exc))
            print(traceback.format_exc())

            try:
                payload = parse_sqs_record(record)
                mark_report_failed(payload, str(exc))
            except Exception:
                print("Could not mark report as failed.")
                print(traceback.format_exc())

            if message_id:
                batch_item_failures.append({
                    "itemIdentifier": message_id
                })

    return {
        "batchItemFailures": batch_item_failures,
        "results": results
    }