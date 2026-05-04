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
registrations_table = dynamodb_resource.Table(REGISTRATIONS_TABLE)

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


def get_path_values(event):
    path_parameters = event.get("pathParameters") or {}

    event_id = path_parameters.get("eventId")
    user_id = path_parameters.get("userId")

    if not event_id:
        raise ValueError("eventId is required.")

    if not user_id:
        raise ValueError("userId is required.")

    return event_id.strip(), user_id.strip()


def validate_user_can_cancel(claims, user_id_from_path):
    token_user_id = claims.get("sub")

    if not token_user_id:
        return response(401, {
            "message": "Unauthorized",
            "reason": "sub claim is required."
        })

    if token_user_id != user_id_from_path:
        return response(403, {
            "message": "Forbidden",
            "reason": "Users can only cancel their own registration."
        })

    return None


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def get_registration(event_id, user_id):
    result = registrations_table.get_item(
        Key={
            "eventId": event_id,
            "userId": user_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def send_registration_cancelled_notification(event_item, registration_item):
    if not NOTIFICATIONS_QUEUE_URL:
        return {
            "status": "SKIPPED",
            "reason": "NOTIFICATIONS_QUEUE_URL is not configured"
        }

    message = {
        "type": "REGISTRATION_CANCELLED",
        "channel": "EMAIL",
        "eventId": registration_item["eventId"],
        "userId": registration_item["userId"],
        "templateType": "REGISTRATION_CANCELLED",
        "to": registration_item.get("email"),
        "templateData": {
            "fullName": registration_item.get("fullName", ""),
            "title": event_item.get("title", ""),
            "startDate": event_item.get("startDate", ""),
            "location": event_item.get("location", ""),
            "eventId": registration_item["eventId"],
        }
    }

    if not message["to"]:
        return {
            "status": "SKIPPED",
            "reason": "registration email is missing"
        }

    params = {
        "QueueUrl": NOTIFICATIONS_QUEUE_URL,
        "MessageBody": json.dumps(message, ensure_ascii=False),
    }

    if NOTIFICATIONS_QUEUE_URL.endswith(".fifo"):
        params["MessageGroupId"] = f"event-{registration_item['eventId']}"
        params["MessageDeduplicationId"] = (
            f"{registration_item['eventId']}-{registration_item['userId']}-registration-cancelled"
        )

    result = sqs.send_message(**params)

    return {
        "status": "QUEUED",
        "messageId": result.get("MessageId")
    }


def cancel_registration_transaction(event_item, registration_item):
    event_id = event_item["eventId"]
    user_id = registration_item["userId"]
    now_iso = iso_z(utc_now())

    updated_registration = {
        **registration_item,
        "status": REGISTRATION_STATUS_CANCELLED,
        "eventStatusKey": f"{event_id}#{REGISTRATION_STATUS_CANCELLED}",
        "updatedAt": now_iso,
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
                        SET availableSlots = availableSlots + :one,
                            updatedAt = :updatedAt
                    """,
                    "ConditionExpression": """
                        attribute_exists(eventId)
                        AND #eventStatus = :active
                        AND registrationOpen = :true
                        AND availableSlots < #capacity
                    """,
                    "ExpressionAttributeNames": {
                        "#eventStatus": "status",
                        "#capacity": "capacity"
                    },
                    "ExpressionAttributeValues": {
                        ":one": {
                            "N": "1"
                        },
                        ":active": {
                            "S": EVENT_STATUS_ACTIVE
                        },
                        ":true": {
                            "BOOL": True
                        },
                        ":updatedAt": {
                            "S": now_iso
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
                        SET #registrationStatus = :cancelled,
                            eventStatusKey = :eventStatusKey,
                            updatedAt = :updatedAt
                    """,
                    "ConditionExpression": """
                        attribute_exists(eventId)
                        AND attribute_exists(userId)
                        AND #registrationStatus = :registered
                    """,
                    "ExpressionAttributeNames": {
                        "#registrationStatus": "status"
                    },
                    "ExpressionAttributeValues": {
                        ":cancelled": {
                            "S": REGISTRATION_STATUS_CANCELLED
                        },
                        ":registered": {
                            "S": REGISTRATION_STATUS_REGISTERED
                        },
                        ":eventStatusKey": {
                            "S": updated_registration["eventStatusKey"]
                        },
                        ":updatedAt": {
                            "S": now_iso
                        }
                    }
                }
            }
        ]
    )

    return updated_registration


def handle_transaction_error(exc):
    return response(409, {
        "message": "Registration could not be cancelled. The event may not be active, registration may be closed, or the registration is not currently REGISTERED."
    })


def handler(event, context):
    try:
        claims, auth_error = require_attendee(event)

        if auth_error:
            return auth_error

        event_id, user_id = get_path_values(event)

        ownership_error = validate_user_can_cancel(claims, user_id)

        if ownership_error:
            return ownership_error

        event_item = get_event(event_id)

        if not event_item:
            return response(404, {
                "message": "Event not found.",
                "eventId": event_id
            })

        registration_item = get_registration(event_id, user_id)

        if not registration_item:
            return response(404, {
                "message": "Registration not found.",
                "eventId": event_id,
                "userId": user_id
            })

        if registration_item.get("status") != REGISTRATION_STATUS_REGISTERED:
            return response(409, {
                "message": "Registration is not active.",
                "currentStatus": registration_item.get("status")
            })

        updated_registration = cancel_registration_transaction(
            event_item=event_item,
            registration_item=registration_item
        )

        notification_result = send_registration_cancelled_notification(
            event_item=event_item,
            registration_item=updated_registration
        )

        return response(200, {
            "message": "Registration cancelled successfully.",
            "registration": updated_registration,
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
            "message": "Error cancelling registration.",
            "awsErrorCode": error_code,
            "error": str(exc)
        })

    except Exception as exc:
        return response(500, {
            "message": "Internal error.",
            "error": str(exc)
        })