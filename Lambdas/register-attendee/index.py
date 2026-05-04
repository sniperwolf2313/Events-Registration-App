import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.client("dynamodb")
dynamodb_resource = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
NOTIFICATIONS_QUEUE_URL = os.environ.get("NOTIFICATIONS_QUEUE_URL")

events_table = dynamodb_resource.Table(EVENTS_TABLE)

EVENT_STATUS_ACTIVE = "ACTIVE"

REGISTRATION_STATUS_REGISTERED = "REGISTERED"
REGISTRATION_STATUS_CANCELLED = "CANCELLED"


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


def require_attendee(event):
    claims = get_claims(event)

    if not claims:
        return None, response(401, {
            "message": "Unauthorized",
            "reason": "No JWT claims found."
        })

    groups = normalize_groups(claims.get("cognito:groups"))

    if "attendees" not in groups:
        return None, response(403, {
            "message": "Forbidden",
            "requiredGroup": "attendees",
            "currentGroups": groups
        })

    return claims, None


def get_event_id(event, body):
    path_parameters = event.get("pathParameters") or {}

    event_id = path_parameters.get("eventId") or body.get("eventId")

    if not event_id:
        raise ValueError("eventId is required.")

    return str(event_id).strip()


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")

    if not item:
        return None

    return decimal_to_native(item)


def get_registration_data(claims, body):
    email = claims.get("email")

    if not email:
        raise ValueError("email claim is required.")

    full_name = body.get("fullName")

    if not full_name:
        raise ValueError("fullName is required.")

    return {
        "userId": claims.get("sub"),
        "email": email,
        "fullName": str(full_name).strip()
    }


def send_registration_confirmation(event_item, registration_item):
    if not NOTIFICATIONS_QUEUE_URL:
        return {
            "status": "SKIPPED",
            "reason": "NOTIFICATIONS_QUEUE_URL is not configured"
        }

    message = {
        "type": "REGISTRATION_CONFIRMATION",
        "channel": "EMAIL",
        "eventId": registration_item["eventId"],
        "userId": registration_item["userId"],
        "templateType": "REGISTRATION_CONFIRMATION",
        "to": registration_item["email"],
        "templateData": {
            "fullName": registration_item["fullName"],
            "title": event_item.get("title", ""),
            "startDate": event_item.get("startDate", ""),
            "location": event_item.get("location", ""),
            "eventId": registration_item["eventId"],
        }
    }

    params = {
        "QueueUrl": NOTIFICATIONS_QUEUE_URL,
        "MessageBody": json.dumps(message, ensure_ascii=False),
    }

    if NOTIFICATIONS_QUEUE_URL.endswith(".fifo"):
        params["MessageGroupId"] = f"event-{registration_item['eventId']}"
        params["MessageDeduplicationId"] = (
            f"{registration_item['eventId']}-{registration_item['userId']}-registration"
        )

    result = sqs.send_message(**params)

    return {
        "status": "QUEUED",
        "messageId": result.get("MessageId")
    }


def register_attendee_transaction(event_item, registration_data):
    event_id = event_item["eventId"]
    user_id = registration_data["userId"]
    now_iso = iso_z(utc_now())

    registration_item = {
        "eventId": event_id,
        "userId": user_id,
        "registrationDate": now_iso,
        "status": REGISTRATION_STATUS_REGISTERED,
        "eventStatusKey": f"{event_id}#{REGISTRATION_STATUS_REGISTERED}",
        "email": registration_data["email"],
        "fullName": registration_data["fullName"],
        "createdAt": now_iso
    }

    dynamodb.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": EVENTS_TABLE,
                    "Key": {
                        "eventId": {
                            "S": event_id
                        }
                    },
                    "UpdateExpression": """
                        SET availableSlots = availableSlots - :one
                    """,
                    "ConditionExpression": """
                        attribute_exists(eventId)
                        AND #eventStatus = :active
                        AND registrationOpen = :true
                        AND availableSlots > :zero
                    """,
                    "ExpressionAttributeNames": {
                        "#eventStatus": "status"
                    },
                    "ExpressionAttributeValues": {
                        ":one": {
                            "N": "1"
                        },
                        ":zero": {
                            "N": "0"
                        },
                        ":active": {
                            "S": EVENT_STATUS_ACTIVE
                        },
                        ":true": {
                            "BOOL": True
                        }
                    }
                }
            },
            {
                "Update": {
                    "TableName": REGISTRATIONS_TABLE,
                    "Key": {
                        "eventId": {
                            "S": event_id
                        },
                        "userId": {
                            "S": user_id
                        }
                    },
                    "UpdateExpression": """
                        SET registrationDate = :registrationDate,
                            #registrationStatus = :registered,
                            eventStatusKey = :eventStatusKey,
                            email = :email,
                            fullName = :fullName,
                            createdAt = if_not_exists(createdAt, :createdAt)
                    """,
                    "ConditionExpression": """
                        attribute_not_exists(eventId)
                        OR #registrationStatus = :cancelled
                    """,
                    "ExpressionAttributeNames": {
                        "#registrationStatus": "status"
                    },
                    "ExpressionAttributeValues": {
                        ":registrationDate": {
                            "S": registration_item["registrationDate"]
                        },
                        ":registered": {
                            "S": REGISTRATION_STATUS_REGISTERED
                        },
                        ":cancelled": {
                            "S": REGISTRATION_STATUS_CANCELLED
                        },
                        ":eventStatusKey": {
                            "S": registration_item["eventStatusKey"]
                        },
                        ":email": {
                            "S": registration_item["email"]
                        },
                        ":fullName": {
                            "S": registration_item["fullName"]
                        },
                        ":createdAt": {
                            "S": registration_item["createdAt"]
                        }
                    }
                }
            }
        ]
    )

    return registration_item


def handle_transaction_error(exc):
    reasons = exc.response.get("CancellationReasons", [])

    event_reason = reasons[0] if len(reasons) > 0 else {}
    registration_reason = reasons[1] if len(reasons) > 1 else {}

    if event_reason.get("Code") == "ConditionalCheckFailed":
        return response(409, {
            "message": "Event is not available for registration."
        })

    if registration_reason.get("Code") == "ConditionalCheckFailed":
        return response(409, {
            "message": "User is already registered for this event."
        })

    return response(409, {
        "message": "Registration could not be completed."
    })


def handler(event, context):
    try:
        claims, auth_error = require_attendee(event)

        if auth_error:
            return auth_error

        body = parse_body(event)
        event_id = get_event_id(event, body)

        event_item = get_event(event_id)

        if not event_item:
            return response(404, {
                "message": "Event not found.",
                "eventId": event_id
            })

        registration_data = get_registration_data(claims, body)

        registration_item = register_attendee_transaction(
            event_item=event_item,
            registration_data=registration_data
        )

        notification_result = send_registration_confirmation(
            event_item=event_item,
            registration_item=registration_item
        )

        return response(201, {
            "message": "Registration completed successfully.",
            "registration": registration_item,
            "notification": notification_result
        })

    except ValueError as exc:
        return response(400, {
            "message": str(exc)
        })

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "TransactionCanceledException":
            return handle_transaction_error(exc)

        return response(500, {
            "message": "Error registering attendee.",
            "awsErrorCode": error_code,
            "error": str(exc)
        })

    except Exception as exc:
        return response(500, {
            "message": "Internal error.",
            "error": str(exc)
        })