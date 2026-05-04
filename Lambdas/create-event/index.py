import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
scheduler = boto3.client("scheduler")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]

# Requeridos si quieres crear schedules desde CreateEvent
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN")
SCHEDULER_GROUP_NAME = os.environ.get("SCHEDULER_GROUP_NAME", "default")
STATUS_UPDATE_FUNCTION_ARN = os.environ.get("STATUS_UPDATE_FUNCTION_ARN")
NOTIFICATIONS_QUEUE_ARN = os.environ.get("NOTIFICATIONS_QUEUE_ARN")

PROJECT_NAME = os.environ.get("PROJECT_NAME", "event-manager")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

events_table = dynamodb.Table(EVENTS_TABLE)


STATUS_ACTIVE = "ACTIVE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_FINISHED = "FINISHED"
STATUS_CANCELLED = "CANCELLED"

STATUS_LABELS = {
    "ACTIVE": "Activo",
    "IN_PROGRESS": "En curso",
    "FINISHED": "Finalizado",
    "CANCELLED": "Cancelado"
}

STATUS_LABELS = {
    "ACTIVE": "Activo",
    "IN_PROGRESS": "En curso",
    "FINISHED": "Finalizado",
    "CANCELLED": "Cancelado"
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }


def parse_body(event):
    body = event.get("body")

    if isinstance(body, str):
        if not body.strip():
            return {}
        return json.loads(body)

    if isinstance(body, dict):
        return body

    if body is None:
        return event

    return {}


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
        return [str(group).strip() for group in groups_value if str(group).strip()]

    if isinstance(groups_value, str):
        value = groups_value.strip()

        # Caso: '["organizers","attendees"]'
        if value.startswith("["):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(group).strip() for group in parsed if str(group).strip()]
            except json.JSONDecodeError:
                pass
        
        value = (
            value.replace("[", "")
            .replace("]", "")
            .replace('"', "")
            .replace("'", "")
        )

        return [
            group.strip()
            for group in re.split(r"[,\s]+", value)
            if group.strip()
        ]

    return []


def require_group(event, required_group):
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )

    if not claims:
        return None, response(401, {
            "message": "Unauthorized",
            "reason": "No JWT claims found"
        })

    groups = normalize_groups(claims.get("cognito:groups"))

    print("Claims:", json.dumps(claims, ensure_ascii=False))
    print("Normalized groups:", groups)

    if required_group not in groups:
        return None, response(403, {
            "message": "Forbidden",
            "requiredGroup": required_group,
            "currentGroups": groups
        })

    return claims, None


def parse_iso_datetime(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required and must be an ISO-8601 datetime string.")

    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(
            f"{field_name} must use ISO-8601 format. Example: 2026-04-25T08:00:00-05:00"
        )

    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include timezone. Example: 2026-04-25T08:00:00-05:00 or 2026-04-25T13:00:00Z"
        )

    return parsed.astimezone(timezone.utc)


def iso_z(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def scheduler_at_expression(dt):
    # EventBridge Scheduler usa at(yyyy-mm-ddThh:mm:ss) con ScheduleExpressionTimezone.
    utc_dt = dt.astimezone(timezone.utc)
    return f"at({utc_dt.strftime('%Y-%m-%dT%H:%M:%S')})"


def safe_schedule_name(event_id, suffix):
    raw_name = f"{PROJECT_NAME}-{ENVIRONMENT}-{event_id}-{suffix}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw_name)
    return safe[:64]


def create_scheduler_target_schedule(
    name,
    schedule_type,
    scheduled_time,
    target_arn,
    role_arn,
    payload,
    target_type,
    sqs_message_group_id=None
):
    now = datetime.now(timezone.utc)

    if scheduled_time <= now:
        return {
            "name": name,
            "type": schedule_type,
            "target": target_type,
            "status": "SKIPPED",
            "reason": "scheduled time is in the past",
            "scheduledAt": iso_z(scheduled_time)
        }

    target = {
        "Arn": target_arn,
        "RoleArn": role_arn,
        "Input": json.dumps(payload, ensure_ascii=False)
    }

    if sqs_message_group_id:
        target["SqsParameters"] = {
            "MessageGroupId": sqs_message_group_id
        }

    scheduler.create_schedule(
        Name=name,
        GroupName=SCHEDULER_GROUP_NAME,
        ScheduleExpression=scheduler_at_expression(scheduled_time),
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={
            "Mode": "OFF"
        },
        Target=target
    )

    return {
        "name": name,
        "type": schedule_type,
        "target": target_type,
        "status": "CREATED",
        "scheduledAt": iso_z(scheduled_time)
    }

def create_event_schedules(event_item):
    if not SCHEDULER_ROLE_ARN:
        return {
            "created": [],
            "skipped": [],
            "errors": ["SCHEDULER_ROLE_ARN is not configured"]
        }

    event_id = event_item["eventId"]
    start_dt = parse_iso_datetime(event_item["startDate"], "startDate")
    end_dt = parse_iso_datetime(event_item["endDate"], "endDate")

    created = []
    skipped = []
    errors = []

    # 1. Cambiar automáticamente a IN_PROGRESS al inicio.
    if STATUS_UPDATE_FUNCTION_ARN:
        start_schedule_name = safe_schedule_name(event_id, "start-status")

        start_payload = {
            "source": "eventbridge-scheduler",
            "action": "UPDATE_EVENT_STATUS",
            "eventId": event_id,
            "targetStatus": STATUS_IN_PROGRESS,
            "reason": "EVENT_START",
            "scheduledAt": iso_z(start_dt)
        }

        try:
            result = create_scheduler_target_schedule(
                name=start_schedule_name,
                schedule_type="START_STATUS",
                scheduled_time=start_dt,
                target_arn=STATUS_UPDATE_FUNCTION_ARN,
                role_arn=SCHEDULER_ROLE_ARN,
                payload=start_payload,
                target_type="LAMBDA"
            )

            if result["status"] == "CREATED":
                created.append(result)
            else:
                skipped.append(result)

        except ClientError as exc:
            errors.append({
                "schedule": start_schedule_name,
                "error": str(exc)
            })

        # 2. Cambiar automáticamente a FINISHED al final.
        end_schedule_name = safe_schedule_name(event_id, "end-status")

        end_payload = {
            "source": "eventbridge-scheduler",
            "action": "UPDATE_EVENT_STATUS",
            "eventId": event_id,
            "targetStatus": STATUS_FINISHED,
            "reason": "EVENT_END",
            "sendThankYouNotification": True,
            "scheduledAt": iso_z(end_dt)
        }

        try:
            result = create_scheduler_target_schedule(
                name=end_schedule_name,
                schedule_type="END_STATUS",
                scheduled_time=end_dt,
                target_arn=STATUS_UPDATE_FUNCTION_ARN,
                role_arn=SCHEDULER_ROLE_ARN,
                payload=end_payload,
                target_type="LAMBDA"
            )

            if result["status"] == "CREATED":
                created.append(result)
            else:
                skipped.append(result)

        except ClientError as exc:
            errors.append({
                "schedule": end_schedule_name,
                "error": str(exc)
            })

    else:
        errors.append("STATUS_UPDATE_FUNCTION_ARN is not configured")

    # 3. Recordatorio 24h y 12h antes.
    if NOTIFICATIONS_QUEUE_ARN:
        reminders = [
            {
                "suffix": "reminder-24h",
                "scheduleType": "REMINDER_24H",
                "hoursBefore": 24,
                "type": "EVENT_REMINDER_24H",
                "templateType": "EVENT_REMINDER_24H",
            },
            {
                "suffix": "reminder-12h",
                "scheduleType": "REMINDER_12H",
                "hoursBefore": 12,
                "type": "EVENT_REMINDER_12H",
                "templateType": "EVENT_REMINDER_12H",
            },
        ]

        for reminder in reminders:
            reminder_time = start_dt - timedelta(hours=reminder["hoursBefore"])
            schedule_name = safe_schedule_name(event_id, reminder["suffix"])

            payload = {
                "type": reminder["type"],
                "channel": "EMAIL",
                "eventId": event_id,
                "templateType": reminder["templateType"],
                "recipientFilter": {
                    "status": "REGISTERED"
                },
                "hoursBefore": reminder["hoursBefore"],
                "scheduledAt": iso_z(reminder_time)
            }

            try:
                result = create_scheduler_target_schedule(
                    name=schedule_name,
                    schedule_type=reminder["scheduleType"],
                    scheduled_time=reminder_time,
                    target_arn=NOTIFICATIONS_QUEUE_ARN,
                    role_arn=SCHEDULER_ROLE_ARN,
                    payload=payload,
                    target_type="SQS",
                    sqs_message_group_id=f"event-{event_id}" if NOTIFICATIONS_QUEUE_ARN.endswith(".fifo") else None
                )

                if result["status"] == "CREATED":
                    created.append(result)
                else:
                    skipped.append(result)

            except ClientError as exc:
                errors.append({
                    "schedule": schedule_name,
                    "error": str(exc)
                })

    else:
        errors.append("NOTIFICATIONS_QUEUE_ARN is not configured")

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors
    }

def validate_payload(body):
    required_fields = [
        "title",
        "description",
        "location",
        "startDate",
        "endDate",
        "capacity"
    ]

    missing = [
        field for field in required_fields
        if field not in body or body[field] in [None, ""]
    ]

    if missing:
        raise ValueError(f"Faltan campos obligatorios: {', '.join(missing)}")

    title = str(body["title"]).strip()
    description = str(body["description"]).strip()
    location = str(body["location"]).strip()

    if len(title) < 3:
        raise ValueError("title debe tener al menos 3 caracteres.")

    if len(title) > 120:
        raise ValueError("title no debe superar 120 caracteres.")

    if len(description) > 1000:
        raise ValueError("description no debe superar 1000 caracteres.")

    capacity = int(body["capacity"])

    if capacity <= 0:
        raise ValueError("capacity debe ser mayor a cero.")

    if capacity > 10000:
        raise ValueError("capacity no debe superar 10000 para este MVP.")

    start_dt = parse_iso_datetime(body["startDate"], "startDate")
    end_dt = parse_iso_datetime(body["endDate"], "endDate")

    if end_dt <= start_dt:
        raise ValueError("endDate debe ser posterior a startDate.")

    duration_minutes = int((end_dt - start_dt).total_seconds() // 60)

    if duration_minutes <= 0:
        raise ValueError("La duración del evento debe ser mayor a cero minutos.")

    return {
        "title": title,
        "description": description,
        "location": location,
        "capacity": capacity,
        "startDate": iso_z(start_dt),
        "endDate": iso_z(end_dt),
        "durationMinutes": duration_minutes
    }


def handler(event, context):
    try:
        claims, auth_error = require_group(event, "organizers")

        if auth_error:
            return auth_error

        body = parse_body(event)
        validated = validate_payload(body)

        now_iso = iso_z(datetime.now(timezone.utc))

        event_id = body.get("eventId") or f"EVT-{uuid.uuid4().hex[:8].upper()}"

        created_by = claims.get("sub")
        created_by_email = claims.get("email", "")

        item = {
            "eventId": event_id,
            "title": validated["title"],
            "description": validated["description"],
            "location": validated["location"],

            "startDate": validated["startDate"],
            "endDate": validated["endDate"],
            "durationMinutes": validated["durationMinutes"],

            "capacity": validated["capacity"],
            "availableSlots": validated["capacity"],

            "status": STATUS_ACTIVE,
            "statusLabel": STATUS_LABELS[STATUS_ACTIVE],
            "registrationOpen": True,

            "createdByEmail": created_by_email,
            "createdAt": now_iso,

            "schedulingDetails": {},
        }

        events_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(eventId)",
        )

        schedule_result = create_event_schedules(item)

        events_table.update_item(
            Key={
                "eventId": event_id
            },
            UpdateExpression="""
                SET schedulingDetails = :schedulingDetails
            """,
            ExpressionAttributeValues={
                ":schedulingDetails": schedule_result

            },
        )

        item["schedulingDetails"] = schedule_result

        return response(201, {
            "message": "Evento creado correctamente",
            "event": item
        })

    except ValueError as exc:
        return response(400, {
            "message": str(exc)
        })

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code == "ConditionalCheckFailedException":
            return response(409, {
                "message": "Ya existe un evento con ese eventId."
            })

        return response(500, {
            "message": "Error al crear el evento",
            "error": str(exc)
        })

    except Exception as exc:
        return response(500, {
            "message": "Error interno",
            "error": str(exc)
        })