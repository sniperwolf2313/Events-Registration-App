import json
import os
import traceback
import re
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
NOTIFICATION_TEMPLATES_TABLE = os.environ["NOTIFICATION_TEMPLATES_TABLE"]

SES_SENDER_EMAIL = os.environ["SES_SENDER_EMAIL"]

GSI_EVENT_STATUS_REGISTRATIONS = os.environ.get(
    "GSI_EVENT_STATUS_REGISTRATIONS",
    "GSI2_EventStatusRegistrations"
)

GSI_TEMPLATE_TYPE_STATUS = os.environ.get(
    "GSI_TEMPLATE_TYPE_STATUS",
    "GSI1_TemplateTypeStatus"
)

REGISTRATION_STATUS_REGISTERED = "REGISTERED"

events_table = dynamodb.Table(EVENTS_TABLE)
registrations_table = dynamodb.Table(REGISTRATIONS_TABLE)
templates_table = dynamodb.Table(NOTIFICATION_TEMPLATES_TABLE)


TEMPLATE_ALIASES = {
    "confirmacion_registro": "REGISTRATION_CONFIRMATION",
    "registration_confirmation": "REGISTRATION_CONFIRMATION",
    "REGISTRATION_CONFIRMATION": "REGISTRATION_CONFIRMATION",

    "cancelacion_registro": "REGISTRATION_CANCELLED",
    "registration_cancelled": "REGISTRATION_CANCELLED",
    "REGISTRATION_CANCELLED": "REGISTRATION_CANCELLED",

    "recordatorio_24h": "EVENT_REMINDER_24H",
    "event_reminder_24h": "EVENT_REMINDER_24H",
    "EVENT_REMINDER_24H": "EVENT_REMINDER_24H",

    "recordatorio_12h": "EVENT_REMINDER_12H",
    "event_reminder_12h": "EVENT_REMINDER_12H",
    "EVENT_REMINDER_12H": "EVENT_REMINDER_12H",

    "agradecimiento": "EVENT_THANK_YOU",
    "event_thank_you": "EVENT_THANK_YOU",
    "EVENT_THANK_YOU": "EVENT_THANK_YOU",

    "cancelacion_evento": "EVENT_CANCELLED",
    "event_cancelled": "EVENT_CANCELLED",
    "EVENT_CANCELLED": "EVENT_CANCELLED",

    "actualizacion_evento": "EVENT_UPDATED",
    "event_updated": "EVENT_UPDATED",
    "EVENT_UPDATED": "EVENT_UPDATED",

    "report_ready": "REPORT_READY",
    "REPORT_READY": "REPORT_READY",
}


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


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


def normalize_template_type(value):
    if not value:
        raise ValueError("templateType or type is required.")

    raw = str(value).strip()
    return TEMPLATE_ALIASES.get(raw, TEMPLATE_ALIASES.get(raw.lower(), raw))


def render_template(template_text, data):
    if not template_text:
        return ""

    def replace_match(match):
        key = match.group(1)
        return str(data.get(key, "{" + key + "}"))

    return re.sub(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
        replace_match,
        template_text
    )


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def get_active_template(template_type):
    canonical_type = normalize_template_type(template_type)
    template_type_status = f"{canonical_type}#ACTIVE"

    result = templates_table.query(
        IndexName=GSI_TEMPLATE_TYPE_STATUS,
        KeyConditionExpression=Key("templateTypeStatus").eq(template_type_status),
        ScanIndexForward=False,
        Limit=1,
    )

    items = result.get("Items", [])

    if not items:
        raise ValueError(f"No ACTIVE template found for templateType: {canonical_type}")

    template = decimal_to_native(items[0])

    subject = template.get("subject")
    html_body = template.get("htmlBody")
    text_body = template.get("textBody")

    if not subject:
        raise ValueError(f"Template {canonical_type} is missing subject.")

    if not html_body and not text_body:
        raise ValueError(f"Template {canonical_type} must have htmlBody or textBody.")

    return {
        "templateId": template.get("templateId"),
        "templateType": canonical_type,
        "subject": subject,
        "htmlBody": html_body,
        "textBody": text_body,
    }


def query_registered_recipients(event_id, status=REGISTRATION_STATUS_REGISTERED):
    event_status_key = f"{event_id}#{status}"

    recipients = []
    last_key = None

    while True:
        params = {
            "IndexName": GSI_EVENT_STATUS_REGISTRATIONS,
            "KeyConditionExpression": Key("eventStatusKey").eq(event_status_key),
        }

        if last_key:
            params["ExclusiveStartKey"] = last_key

        result = registrations_table.query(**params)

        recipients.extend([
            decimal_to_native(item)
            for item in result.get("Items", [])
        ])

        last_key = result.get("LastEvaluatedKey")

        if not last_key:
            break

    return recipients


def build_template_data(message, event_item=None, registration_item=None):
    data = {}

    if event_item:
        data.update({
            "eventId": event_item.get("eventId", ""),
            "title": event_item.get("title", ""),
            "description": event_item.get("description", ""),
            "location": event_item.get("location", ""),
            "startDate": event_item.get("startDate", ""),
            "endDate": event_item.get("endDate", ""),
            "status": event_item.get("status", ""),
            "statusLabel": event_item.get("statusLabel", ""),
        })

    if registration_item:
        data.update({
            "userId": registration_item.get("userId", ""),
            "email": registration_item.get("email", ""),
            "fullName": registration_item.get("fullName", ""),
            "registrationDate": registration_item.get("registrationDate", ""),
            "registrationStatus": registration_item.get("status", ""),
        })

    data.update(message.get("templateData") or {})

    data.update({
        "customMessage": message.get("customMessage", ""),
        "notificationType": message.get("type", ""),
        "generatedAt": utc_now_iso(),
    })

    return data


def send_email(to_address, subject, text_body=None, html_body=None):
    if not to_address:
        raise ValueError("to address is required.")

    body = {}

    if text_body:
        body["Text"] = {
            "Charset": "UTF-8",
            "Data": text_body,
        }

    if html_body:
        body["Html"] = {
            "Charset": "UTF-8",
            "Data": html_body,
        }

    if not body:
        raise ValueError("Email body is empty.")

    result = ses.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={
            "ToAddresses": [
                to_address
            ]
        },
        Message={
            "Subject": {
                "Charset": "UTF-8",
                "Data": subject,
            },
            "Body": body,
        },
    )

    return result.get("MessageId")


def build_email_content(message, event_item=None, registration_item=None):
    template_type = normalize_template_type(
        message.get("templateType") or message.get("type")
    )

    template = get_active_template(template_type)
    template_data = build_template_data(message, event_item, registration_item)

    subject = render_template(template["subject"], template_data)
    html_body = render_template(template.get("htmlBody"), template_data)
    text_body = render_template(template.get("textBody"), template_data)

    return {
        "templateId": template["templateId"],
        "templateType": template_type,
        "subject": subject,
        "htmlBody": html_body,
        "textBody": text_body,
    }


def process_direct_notification(message):
    event_id = message.get("eventId")
    event_item = get_event(event_id) if event_id else None

    to_address = message.get("to")

    if not to_address:
        raise ValueError("Direct notification requires 'to'.")

    pseudo_registration = {
        "userId": message.get("userId", ""),
        "email": to_address,
        "fullName": (message.get("templateData") or {}).get("fullName", ""),
        "registrationDate": (message.get("templateData") or {}).get("registrationDate", ""),
        "status": REGISTRATION_STATUS_REGISTERED,
    }

    content = build_email_content(
        message=message,
        event_item=event_item,
        registration_item=pseudo_registration,
    )

    message_id = send_email(
        to_address=to_address,
        subject=content["subject"],
        text_body=content["textBody"],
        html_body=content["htmlBody"],
    )

    return {
        "mode": "DIRECT",
        "sent": 1,
        "failed": 0,
        "messageIds": [
            message_id
        ],
        "templateId": content["templateId"],
        "templateType": content["templateType"],
    }


def process_event_notification(message):
    event_id = message.get("eventId")

    if not event_id:
        raise ValueError("eventId is required for event notifications.")

    event_item = get_event(event_id)

    if not event_item:
        raise ValueError(f"Event not found: {event_id}")

    recipient_filter = message.get("recipientFilter") or {}
    registration_status = recipient_filter.get("status", REGISTRATION_STATUS_REGISTERED)

    recipients = query_registered_recipients(
        event_id=event_id,
        status=registration_status,
    )

    sent = []
    failed = []

    for recipient in recipients:
        to_address = recipient.get("email")

        if not to_address:
            failed.append({
                "userId": recipient.get("userId"),
                "reason": "registration email is missing",
            })
            continue

        try:
            content = build_email_content(
                message=message,
                event_item=event_item,
                registration_item=recipient,
            )

            message_id = send_email(
                to_address=to_address,
                subject=content["subject"],
                text_body=content["textBody"],
                html_body=content["htmlBody"],
            )

            sent.append({
                "userId": recipient.get("userId"),
                "email": to_address,
                "messageId": message_id,
                "templateId": content["templateId"],
                "templateType": content["templateType"],
            })

        except Exception as exc:
            failed.append({
                "userId": recipient.get("userId"),
                "email": to_address,
                "error": str(exc),
            })

    if recipients and not sent:
        raise RuntimeError(json.dumps({
            "message": "No notifications could be sent.",
            "failed": failed,
        }, ensure_ascii=False))

    return {
        "mode": "EVENT_BROADCAST",
        "eventId": event_id,
        "recipientFilter": {
            "status": registration_status
        },
        "recipients": len(recipients),
        "sent": len(sent),
        "failed": len(failed),
        "sentItems": sent,
        "failedItems": failed,
    }


def process_notification(message):
    notification_type = normalize_template_type(message.get("type"))

    direct_types = {
        "REGISTRATION_CONFIRMATION",
        "REGISTRATION_CANCELLED",
        "REPORT_READY",
    }

    if notification_type in direct_types or message.get("to"):
        message["type"] = notification_type
        message["templateType"] = normalize_template_type(
            message.get("templateType") or notification_type
        )
        return process_direct_notification(message)

    event_broadcast_types = {
        "EVENT_REMINDER_24H",
        "EVENT_REMINDER_12H",
        "EVENT_THANK_YOU",
        "EVENT_CANCELLED",
        "EVENT_UPDATED",
    }

    if notification_type in event_broadcast_types:
        message["type"] = notification_type
        message["templateType"] = normalize_template_type(
            message.get("templateType") or notification_type
        )
        return process_event_notification(message)

    raise ValueError(f"Unsupported notification type: {notification_type}")


def parse_sqs_record(record):
    body = parse_json(record.get("body"))

    if isinstance(body.get("Message"), str):
        return json.loads(body["Message"])

    return body


def handler(event, context):
    if "Records" in event:
        batch_item_failures = []
        results = []

        for record in event["Records"]:
            message_id = record.get("messageId")

            try:
                message = parse_sqs_record(record)

                print("Processing notification:")
                print(json.dumps(message, ensure_ascii=False))

                result = process_notification(message)

                print("Notification result:")
                print(json.dumps(result, ensure_ascii=False))

                results.append(result)

            except Exception as exc:
                print("Error processing notification:")
                print(str(exc))
                print(traceback.format_exc())

                if message_id:
                    batch_item_failures.append({
                        "itemIdentifier": message_id
                    })

        return {
            "batchItemFailures": batch_item_failures,
            "results": results,
        }

    try:
        message = parse_json(event.get("body")) if "body" in event else event
        result = process_notification(message)

        return response(200, {
            "message": "Notification processed.",
            "result": result,
        })

    except Exception as exc:
        print("Error processing direct notification:")
        print(str(exc))
        print(traceback.format_exc())

        return response(500, {
            "message": "Error processing notification.",
            "error": str(exc),
        })