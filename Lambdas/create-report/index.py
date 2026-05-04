import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REPORTS_TABLE = os.environ["REPORTS_TABLE"]
REPORT_QUEUE_URL = os.environ["REPORT_QUEUE_URL"]

events_table = dynamodb.Table(EVENTS_TABLE)
reports_table = dynamodb.Table(REPORTS_TABLE)

REPORT_TYPE_EVENT_REGISTRATIONS = "EVENT_REGISTRATIONS"


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


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


def parse_body(event):
    body = event.get("body")

    if isinstance(body, str):
        if not body.strip():
            return {}
        return json.loads(body)

    if isinstance(body, dict):
        return body

    if "requestContext" in event:
        return {}

    return event


def get_claims(event):
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def normalize_groups(groups_value):
    if not groups_value:
        return []

    if isinstance(groups_value, list):
        normalized = []

        for group in groups_value:
            value = str(group).strip()
            value = (
                value.replace("[", "")
                .replace("]", "")
                .replace('"', "")
                .replace("'", "")
            )

            normalized.extend([
                item.strip()
                for item in re.split(r"[,\s]+", value)
                if item.strip()
            ])

        return normalized

    if isinstance(groups_value, str):
        value = (
            groups_value.strip()
            .replace("[", "")
            .replace("]", "")
            .replace('"', "")
            .replace("'", "")
        )

        return [
            item.strip()
            for item in re.split(r"[,\s]+", value)
            if item.strip()
        ]

    return []


def require_organizer(event):
    claims = get_claims(event)

    if not claims:
        return None, response(401, {
            "message": "Unauthorized",
            "reason": "No JWT claims found."
        })

    groups = normalize_groups(claims.get("cognito:groups"))

    if "organizers" not in groups:
        return None, response(403, {
            "message": "Forbidden",
            "requiredGroup": "organizers",
            "currentGroups": groups
        })

    return claims, None


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def put_report_metadata(report_item):
    reports_table.put_item(
        Item=report_item,
        ConditionExpression="attribute_not_exists(reportId)"
    )


def send_report_request(report_item):
    message_body = {
        "reportId": report_item["reportId"],
        "reportType": report_item["reportType"],
        "format": report_item["format"],
        "eventId": report_item["eventId"],
        "requestedByEmail": report_item["requestedByEmail"],
        "createdAt": report_item["createdAt"],
    }

    params = {
        "QueueUrl": REPORT_QUEUE_URL,
        "MessageBody": json.dumps(message_body, ensure_ascii=False)
    }

    if REPORT_QUEUE_URL.endswith(".fifo"):
        # Un solo MessageGroupId garantiza orden global de llegada.
        params["MessageGroupId"] = "reports"
        params["MessageDeduplicationId"] = report_item["reportId"]

    result = sqs.send_message(**params)

    return {
        "messageId": result.get("MessageId")
    }


def handler(event, context):
    try:
        claims, auth_error = require_organizer(event)

        if auth_error:
            return auth_error

        body = parse_body(event)

        event_id = body.get("eventId")

        if not event_id:
            return response(400, {
                "message": "eventId is required."
            })

        report_type = body.get("reportType", REPORT_TYPE_EVENT_REGISTRATIONS)
        report_format = body.get("format", "CSV").upper()

        if report_type != REPORT_TYPE_EVENT_REGISTRATIONS:
            return response(400, {
                "message": "Unsupported reportType.",
                "allowedReportTypes": [
                    REPORT_TYPE_EVENT_REGISTRATIONS
                ]
            })

        if report_format != "CSV":
            return response(400, {
                "message": "Unsupported format.",
                "allowedFormats": [
                    "CSV"
                ]
            })

        event_item = get_event(event_id)

        if not event_item:
            return response(404, {
                "message": "Event not found.",
                "eventId": event_id
            })

        requested_by = claims.get("sub")
        requested_by_email = claims.get("email")

        if not requested_by_email:
            return response(400, {
                "message": "email claim is required."
            })

        now_iso = iso_z(utc_now())
        report_id = f"RPT-{uuid.uuid4().hex[:10].upper()}"

        report_item = {
            "reportId": report_id,
            "reportType": report_type,
            "format": report_format,
            "eventId": event_id,
            "requestedByEmail": requested_by_email,
            "createdAt": now_iso
        }

        put_report_metadata(report_item)
        queue_result = send_report_request(report_item)

        return response(202, {
            "message": "Report request accepted.",
            "report": report_item
        })

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return response(409, {
                "message": "Report already exists."
            })

        return response(500, {
            "message": "Error creating report request.",
            "awsErrorCode": error_code,
            "error": str(exc)
        })

    except Exception as exc:
        return response(500, {
            "message": "Internal error.",
            "error": str(exc)
        })