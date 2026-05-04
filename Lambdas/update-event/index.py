import json
import os
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
scheduler = boto3.client("scheduler")
sqs = boto3.client("sqs")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]

PROJECT_NAME = os.environ.get("PROJECT_NAME", "event-manager")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

SCHEDULER_GROUP_NAME = os.environ.get("SCHEDULER_GROUP_NAME", "default")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN")
STATUS_UPDATE_FUNCTION_ARN = os.environ.get("STATUS_UPDATE_FUNCTION_ARN")
NOTIFICATIONS_QUEUE_ARN = os.environ.get("NOTIFICATIONS_QUEUE_ARN")
NOTIFICATIONS_QUEUE_URL = os.environ.get("NOTIFICATIONS_QUEUE_URL")

events_table = dynamodb.Table(EVENTS_TABLE)

STATUS_ACTIVE = "ACTIVE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_FINISHED = "FINISHED"
STATUS_CANCELLED = "CANCELLED"

STATUS_LABELS = {
    STATUS_ACTIVE: "Activo",
    STATUS_IN_PROGRESS: "En curso",
    STATUS_FINISHED: "Finalizado",
    STATUS_CANCELLED: "Cancelado",
}

VALID_STATUSES = {
    STATUS_ACTIVE,
    STATUS_IN_PROGRESS,
    STATUS_FINISHED,
    STATUS_CANCELLED,
}

MANUAL_ALLOWED_STATUSES = {
    STATUS_FINISHED,
    STATUS_CANCELLED,
}


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

            if value.startswith("[") and '"' in value:
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        normalized.extend([
                            str(item).strip()
                            for item in parsed
                            if str(item).strip()
                        ])
                        continue
                except json.JSONDecodeError:
                    pass

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
        value = groups_value.strip()

        if value.startswith("[") and '"' in value:
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [
                        str(item).strip()
                        for item in parsed
                        if str(item).strip()
                    ]
            except json.JSONDecodeError:
                pass

        value = (
            value.replace("[", "")
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


def is_scheduler_event(event):
    return (
        event.get("source") == "eventbridge-scheduler"
        and event.get("action") == "UPDATE_EVENT_STATUS"
    )


def parse_iso_datetime(value, field_name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field_name} is required. Example: 2026-05-03T11:00:00-05:00"
        )

    normalized = value.strip().replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(
            f"{field_name} must use ISO-8601 format. "
            f"Example: 2026-05-03T11:00:00-05:00"
        )

    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include timezone. "
            f"Example: 2026-05-03T11:00:00-05:00 or 2026-05-03T16:00:00Z"
        )

    return parsed.astimezone(timezone.utc)


def scheduler_at_expression(dt):
    utc_dt = dt.astimezone(timezone.utc)
    return f"at({utc_dt.strftime('%Y-%m-%dT%H:%M:%S')})"


def safe_schedule_name(event_id, suffix):
    raw_name = f"{PROJECT_NAME}-{ENVIRONMENT}-{event_id}-{suffix}"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw_name)
    return safe[:64]


def get_event(event_id):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")
    return decimal_to_native(item) if item else None


def get_http_method(event):
    return (
        event.get("requestContext", {})
        .get("http", {})
        .get("method", "")
        .upper()
    )


def send_notification_message(message):
    if not NOTIFICATIONS_QUEUE_URL:
        raise ValueError("NOTIFICATIONS_QUEUE_URL is not configured")

    params = {
        "QueueUrl": NOTIFICATIONS_QUEUE_URL,
        "MessageBody": json.dumps(message, ensure_ascii=False),
    }

    if NOTIFICATIONS_QUEUE_URL.endswith(".fifo"):
        event_id = message.get("eventId", "event")
        message_type = message.get("type", "notification")
        params["MessageGroupId"] = f"event-{event_id}"
        params["MessageDeduplicationId"] = (
            f"{event_id}-{message_type}-{int(utc_now().timestamp())}"
        )

    return sqs.send_message(**params)


def enqueue_thank_you_notification(event_item):
    event_id = event_item["eventId"]

    message = {
        "type": "EVENT_THANK_YOU",
        "channel": "EMAIL",
        "eventId": event_id,
        "templateType": "agradecimiento",
        "reason": "EVENT_FINISHED",
        "recipientFilter": {
            "registrationStatus": "REGISTERED",
            "attendanceStatus": "ATTENDED"
        }
    }

    try:
        sqs_response = send_notification_message(message)

        events_table.update_item(
            Key={
                "eventId": event_id
            },
            UpdateExpression="""
                SET thankYouNotificationStatus = :status,
                    thankYouNotificationQueuedAt = :queuedAt,
                    thankYouNotificationMessageId = :messageId,
                    updatedAt = :updatedAt
            """,
            ConditionExpression="""
                attribute_not_exists(thankYouNotificationStatus)
                OR thankYouNotificationStatus <> :status
            """,
            ExpressionAttributeValues={
                ":status": "QUEUED",
                ":queuedAt": iso_z(utc_now()),
                ":messageId": sqs_response.get("MessageId"),
                ":updatedAt": iso_z(utc_now()),
            },
        )

        return {
            "status": "QUEUED",
            "messageId": sqs_response.get("MessageId"),
        }

    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {
                "status": "SKIPPED",
                "reason": "Thank you notification was already queued",
            }

        raise


def enqueue_cancelled_notification(event_item):
    event_id = event_item["eventId"]

    message = {
        "type": "EVENT_CANCELLED",
        "channel": "EMAIL",
        "eventId": event_id,
        "templateType": "cancelacion_evento",
        "reason": "EVENT_CANCELLED",
        "recipientFilter": {
            "registrationStatus": "REGISTERED"
        }
    }

    try:
        sqs_response = send_notification_message(message)

        return {
            "status": "QUEUED",
            "messageId": sqs_response.get("MessageId"),
        }

    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {
                "status": "SKIPPED",
                "reason": "Cancel notification was already queued",
            }

        raise


def enqueue_event_updated_notification(event_item, update_summary, custom_message=None):
    message = {
        "type": "EVENT_UPDATED",
        "channel": "EMAIL",
        "eventId": event_item["eventId"],
        "templateType": "actualizacion_evento",
        "reason": "EVENT_UPDATED_BY_ORGANIZER",
        "updateSummary": update_summary,
        "customMessage": custom_message or "",
        "recipientFilter": {
            "registrationStatus": "REGISTERED"
        }
    }

    sqs_response = send_notification_message(message)

    return {
        "status": "QUEUED",
        "messageId": sqs_response.get("MessageId"),
    }


def get_created_schedules(event_item):
    scheduling_details = event_item.get("schedulingDetails", {})
    created = scheduling_details.get("created", [])

    return [
        schedule
        for schedule in created
        if schedule.get("status") == "CREATED" and schedule.get("name")
    ]


def delete_event_schedules(event_item):
    deleted = []
    errors = []

    for schedule in get_created_schedules(event_item):
        schedule_name = schedule["name"]

        try:
            scheduler.delete_schedule(
                Name=schedule_name,
                GroupName=SCHEDULER_GROUP_NAME,
            )

            deleted.append({
                "name": schedule_name,
                "type": schedule.get("type"),
                "status": "DELETED",
            })

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")

            if error_code == "ResourceNotFoundException":
                deleted.append({
                    "name": schedule_name,
                    "type": schedule.get("type"),
                    "status": "ALREADY_DELETED",
                })
            else:
                errors.append({
                    "name": schedule_name,
                    "type": schedule.get("type"),
                    "error": str(exc),
                })

    return {
        "deleted": deleted,
        "errors": errors,
    }


def create_scheduler_target_schedule(
    name,
    schedule_type,
    scheduled_time,
    target_arn,
    role_arn,
    payload,
    target_type,
    sqs_message_group_id=None,
):
    if scheduled_time <= utc_now():
        return {
            "name": name,
            "type": schedule_type,
            "target": target_type,
            "status": "SKIPPED",
            "reason": "scheduled time is in the past",
            "scheduledAt": iso_z(scheduled_time),
        }

    target = {
        "Arn": target_arn,
        "RoleArn": role_arn,
        "Input": json.dumps(payload, ensure_ascii=False),
    }

    if sqs_message_group_id:
        target["SqsParameters"] = {
            "MessageGroupId": sqs_message_group_id,
        }

    scheduler.create_schedule(
        Name=name,
        GroupName=SCHEDULER_GROUP_NAME,
        ScheduleExpression=scheduler_at_expression(scheduled_time),
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={
            "Mode": "OFF",
        },
        Target=target,
    )

    return {
        "name": name,
        "type": schedule_type,
        "target": target_type,
        "status": "CREATED",
        "scheduledAt": iso_z(scheduled_time),
    }


def create_event_schedules(event_item):
    if not SCHEDULER_ROLE_ARN:
        return {
            "created": [],
            "skipped": [],
            "errors": ["SCHEDULER_ROLE_ARN is not configured"],
        }

    if not STATUS_UPDATE_FUNCTION_ARN:
        return {
            "created": [],
            "skipped": [],
            "errors": ["STATUS_UPDATE_FUNCTION_ARN is not configured"],
        }

    if not NOTIFICATIONS_QUEUE_ARN:
        return {
            "created": [],
            "skipped": [],
            "errors": ["NOTIFICATIONS_QUEUE_ARN is not configured"],
        }

    event_id = event_item["eventId"]
    start_dt = parse_iso_datetime(event_item["startDate"], "startDate")
    end_dt = parse_iso_datetime(event_item["endDate"], "endDate")

    created = []
    skipped = []
    errors = []

    definitions = [
        {
            "name": safe_schedule_name(event_id, "start-status"),
            "type": "START_STATUS",
            "target": "LAMBDA",
            "scheduledTime": start_dt,
            "targetArn": STATUS_UPDATE_FUNCTION_ARN,
            "payload": {
                "source": "eventbridge-scheduler",
                "action": "UPDATE_EVENT_STATUS",
                "eventId": event_id,
                "targetStatus": STATUS_IN_PROGRESS,
                "reason": "EVENT_START",
                "scheduledAt": iso_z(start_dt),
            },
        },
        {
            "name": safe_schedule_name(event_id, "end-status"),
            "type": "END_STATUS",
            "target": "LAMBDA",
            "scheduledTime": end_dt,
            "targetArn": STATUS_UPDATE_FUNCTION_ARN,
            "payload": {
                "source": "eventbridge-scheduler",
                "action": "UPDATE_EVENT_STATUS",
                "eventId": event_id,
                "targetStatus": STATUS_FINISHED,
                "reason": "EVENT_END",
                "sendThankYouNotification": True,
                "scheduledAt": iso_z(end_dt),
            },
        },
        {
            "name": safe_schedule_name(event_id, "reminder-24h"),
            "type": "REMINDER_24H",
            "target": "SQS",
            "scheduledTime": start_dt - timedelta(hours=24),
            "targetArn": NOTIFICATIONS_QUEUE_ARN,
            "payload": {
                "type": "EVENT_REMINDER_24H",
                "channel": "EMAIL",
                "eventId": event_id,
                "templateType": "recordatorio_24h",
                "hoursBefore": 24,
                "scheduledAt": iso_z(start_dt - timedelta(hours=24)),
            },
        },
        {
            "name": safe_schedule_name(event_id, "reminder-12h"),
            "type": "REMINDER_12H",
            "target": "SQS",
            "scheduledTime": start_dt - timedelta(hours=12),
            "targetArn": NOTIFICATIONS_QUEUE_ARN,
            "payload": {
                "type": "EVENT_REMINDER_12H",
                "channel": "EMAIL",
                "eventId": event_id,
                "templateType": "recordatorio_12h",
                "hoursBefore": 12,
                "scheduledAt": iso_z(start_dt - timedelta(hours=12)),
            },
        },
    ]

    for definition in definitions:
        try:
            result = create_scheduler_target_schedule(
                name=definition["name"],
                schedule_type=definition["type"],
                scheduled_time=definition["scheduledTime"],
                target_arn=definition["targetArn"],
                role_arn=SCHEDULER_ROLE_ARN,
                payload=definition["payload"],
                target_type=definition["target"],
                sqs_message_group_id=(
                    f"event-{event_id}"
                    if definition["target"] == "SQS"
                    and NOTIFICATIONS_QUEUE_ARN.endswith(".fifo")
                    else None
                ),
            )

            if result["status"] == "CREATED":
                created.append(result)
            else:
                skipped.append(result)

        except ClientError as exc:
            errors.append({
                "name": definition["name"],
                "type": definition["type"],
                "target": definition["target"],
                "error": str(exc),
            })

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


def validate_datetime_update(body, current_event):
    start_date = body.get("startDate", current_event.get("startDate"))
    end_date = body.get("endDate", current_event.get("endDate"))

    start_dt = parse_iso_datetime(start_date, "startDate")
    end_dt = parse_iso_datetime(end_date, "endDate")

    if end_dt <= start_dt:
        raise ValueError("endDate debe ser posterior a startDate.")

    return {
        "startDate": iso_z(start_dt),
        "endDate": iso_z(end_dt),
        "durationMinutes": int((end_dt - start_dt).total_seconds() // 60),
    }


def validate_capacity_update(body, current_event):
    if "capacity" not in body:
        return None

    try:
        new_capacity = int(body["capacity"])
    except (TypeError, ValueError):
        raise ValueError("capacity debe ser un número entero.")

    if new_capacity <= 0:
        raise ValueError("capacity debe ser mayor a cero.")

    old_capacity = int(current_event.get("capacity", 0))
    old_available_slots = int(current_event.get("availableSlots", 0))
    registered_count = old_capacity - old_available_slots

    if new_capacity < registered_count:
        raise ValueError(
            f"capacity no puede ser menor al número de inscritos actuales: {registered_count}"
        )

    return {
        "capacity": new_capacity,
        "availableSlots": new_capacity - registered_count,
    }


def normalize_status(value):
    if not value:
        return None

    status = str(value).strip().upper()

    aliases = {
        "ACTIVO": STATUS_ACTIVE,
        "ACTIVE": STATUS_ACTIVE,
        "ABIERTO": STATUS_ACTIVE,

        "EN_CURSO": STATUS_IN_PROGRESS,
        "EN-CURSO": STATUS_IN_PROGRESS,
        "IN_PROGRESS": STATUS_IN_PROGRESS,
        "IN-PROGRESS": STATUS_IN_PROGRESS,

        "FINALIZADO": STATUS_FINISHED,
        "FINISHED": STATUS_FINISHED,

        "CANCELADO": STATUS_CANCELLED,
        "CANCELLED": STATUS_CANCELLED,
        "CANCELED": STATUS_CANCELLED,
    }

    return aliases.get(status)


def update_event_status(
    event_item,
    target_status,
    send_thank_you_notification=False
):
    event_id = event_item["eventId"]
    current_status = event_item.get("status")

    if target_status not in VALID_STATUSES:
        return response(400, {
            "message": "Estado inválido",
            "targetStatus": target_status,
        })

    if current_status == target_status:
        return response(200, {
            "message": "El evento ya está en el estado solicitado.",
            "eventId": event_id,
            "currentStatus": current_status,
        })

    if current_status == STATUS_CANCELLED:
        return response(200, {
            "message": "El evento está cancelado. No se actualizó el estado.",
            "eventId": event_id,
            "currentStatus": current_status,
        })

    if current_status == STATUS_FINISHED and target_status == STATUS_CANCELLED:
        return response(409, {
            "message": "No se puede cancelar un evento que ya está finalizado.",
            "eventId": event_id,
            "currentStatus": current_status,
        })

    if current_status == STATUS_FINISHED:
        return response(200, {
            "message": "El evento ya está finalizado. No se actualizó el estado.",
            "eventId": event_id,
            "currentStatus": current_status,
        })

    if target_status == STATUS_IN_PROGRESS and current_status != STATUS_ACTIVE:
        return response(200, {
            "message": "Solo eventos activos pueden pasar automáticamente a En curso.",
            "eventId": event_id,
            "currentStatus": current_status,
        })

    registration_open = target_status == STATUS_ACTIVE
    now_iso = iso_z(utc_now())

    update_result = events_table.update_item(
        Key={
            "eventId": event_id
        },
        UpdateExpression="""
            SET #status = :targetStatus,
                statusLabel = :statusLabel,
                registrationOpen = :registrationOpen,
                updatedAt = :updatedAt
        """,
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":targetStatus": target_status,
            ":statusLabel": STATUS_LABELS[target_status],
            ":registrationOpen": registration_open,
            ":updatedAt": now_iso,
        },
        ReturnValues="ALL_NEW",
    )

    updated_event = decimal_to_native(update_result["Attributes"])

    side_effects = {}

    if target_status == STATUS_FINISHED and send_thank_you_notification:
        side_effects["thankYouNotification"] = enqueue_thank_you_notification(updated_event)

    if target_status == STATUS_CANCELLED:
        side_effects["cancelNotification"] = enqueue_cancelled_notification(updated_event)
        side_effects["deletedSchedules"] = delete_event_schedules(updated_event)

    if target_status == STATUS_FINISHED:
        side_effects["deletedSchedules"] = delete_event_schedules(updated_event)

    final_event = get_event(event_id)

    return response(200, {
        "message": "Estado del evento actualizado correctamente",
        "event": final_event,
        "sideEffects": side_effects,
    })


def handle_scheduler_status_update(event):
    event_id = event.get("eventId")
    target_status = event.get("targetStatus")
    send_thank_you = bool(event.get("sendThankYouNotification", False))

    if not event_id:
        return response(400, {
            "message": "eventId is required"
        })

    event_item = get_event(event_id)

    if not event_item:
        return response(404, {
            "message": "Evento no encontrado",
            "eventId": event_id,
        })

    return update_event_status(
        event_item=event_item,
        target_status=target_status,
        send_thank_you_notification=send_thank_you
    )


def build_update_expression(update_values):
    expression_attribute_names = {}
    expression_attribute_values = {}
    parts = []

    for index, (field, value) in enumerate(update_values.items()):
        name_placeholder = f"#f{index}"
        value_placeholder = f":v{index}"

        expression_attribute_names[name_placeholder] = field
        expression_attribute_values[value_placeholder] = value
        parts.append(f"{name_placeholder} = {value_placeholder}")

    return (
        "SET " + ", ".join(parts),
        expression_attribute_names,
        expression_attribute_values,
    )


def validate_manual_update_fields(body):
    allowed_fields = {
        "eventId",
        "title",
        "description",
        "location",
        "startDate",
        "endDate",
        "capacity",
        "status",
        "notifyAttendees",
        "notificationMessage",
    }

    unknown_fields = [
        field for field in body.keys()
        if field not in allowed_fields
    ]

    if unknown_fields:
        raise ValueError(
            f"Hay campos no permitidos: {', '.join(unknown_fields)}"
        )


def handle_manual_update(event):
    claims, auth_error = require_group(event, "organizers")

    if auth_error:
        return auth_error

    path_parameters = event.get("pathParameters") or {}
    body = parse_body(event)

    event_id = path_parameters.get("eventId") or body.get("eventId")

    if not event_id:
        return response(400, {
            "message": "eventId es obligatorio"
        })

    http_method = get_http_method(event)

    if http_method == "DELETE":
        body = {
            "eventId": event_id,
            "status": STATUS_CANCELLED
        }

    validate_manual_update_fields(body)

    current_event = get_event(event_id)

    if not current_event:
        return response(404, {
            "message": "Evento no encontrado",
            "eventId": event_id,
        })

    requested_status = normalize_status(body.get("status"))

    if requested_status:
        non_status_fields = [
            field for field in body.keys()
            if field not in ["eventId", "status"]
        ]

        if non_status_fields:
            return response(400, {
                "message": "No mezcles cambio de estado con actualización de datos básicos.",
                "fieldsNotAllowedWithStatus": non_status_fields,
            })

        if requested_status not in MANUAL_ALLOWED_STATUSES:
            return response(400, {
                "message": "Desde actualización manual solo se permite FINISHED o CANCELLED.",
                "allowedStatuses": sorted(MANUAL_ALLOWED_STATUSES),
            })

        return update_event_status(
            event_item=current_event,
            target_status=requested_status,
            send_thank_you_notification=requested_status == STATUS_FINISHED
        )

    if current_event.get("status") != STATUS_ACTIVE:
        return response(409, {
            "message": "Solo se pueden editar datos básicos de eventos activos.",
            "currentStatus": current_event.get("status"),
        })

    update_values = {}

    if "title" in body:
        title = str(body["title"]).strip()

        if len(title) < 3:
            raise ValueError("title debe tener al menos 3 caracteres.")

        if len(title) > 120:
            raise ValueError("title no debe superar 120 caracteres.")

        update_values["title"] = title

    if "description" in body:
        description = str(body["description"]).strip()

        if len(description) > 1000:
            raise ValueError("description no debe superar 1000 caracteres.")

        update_values["description"] = description

    if "location" in body:
        location = str(body["location"]).strip()

        if not location:
            raise ValueError("location no puede estar vacío.")

        update_values["location"] = location

    dates_changed = "startDate" in body or "endDate" in body

    if dates_changed:
        update_values.update(validate_datetime_update(body, current_event))

    capacity_values = validate_capacity_update(body, current_event)

    if capacity_values:
        update_values.update(capacity_values)

    if not update_values:
        return response(400, {
            "message": "No hay campos válidos para actualizar."
        })

    update_values["updatedAt"] = iso_z(utc_now())
    update_values["updatedBy"] = claims.get("email", "")

    update_expression, names, values = build_update_expression(update_values)

    update_result = events_table.update_item(
        Key={
            "eventId": event_id
        },
        UpdateExpression=update_expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )

    updated_event = decimal_to_native(update_result["Attributes"])

    side_effects = {}

    if dates_changed:
        delete_result = delete_event_schedules(current_event)
        schedule_result = create_event_schedules(updated_event)

        reschedule_result = events_table.update_item(
            Key={
                "eventId": event_id
            },
            UpdateExpression="""
                SET schedulingDetails = :schedulingDetails,
                    updatedAt = :updatedAt
            """,
            ExpressionAttributeValues={
                ":schedulingDetails": schedule_result,
                ":updatedAt": iso_z(utc_now()),
            },
            ReturnValues="ALL_NEW",
        )

        updated_event = decimal_to_native(reschedule_result["Attributes"])
        side_effects["deletedSchedules"] = delete_result
        side_effects["newSchedules"] = schedule_result

    if body.get("notifyAttendees") is True:
        side_effects["eventUpdatedNotification"] = enqueue_event_updated_notification(
            event_item=updated_event,
            update_summary={
                "updatedFields": sorted(update_values.keys()),
                "datesChanged": dates_changed,
            },
            custom_message=body.get("notificationMessage"),
        )

    return response(200, {
        "message": "Evento actualizado correctamente",
        "event": get_event(event_id),
        "sideEffects": side_effects,
    })


def handler(event, context):
    try:
        if is_scheduler_event(event):
            return handle_scheduler_status_update(event)

        return handle_manual_update(event)

    except ValueError as exc:
        return response(400, {
            "message": str(exc),
        })

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        return response(500, {
            "message": "Error al actualizar el evento",
            "awsErrorCode": error_code,
            "error": str(exc),
        })

    except Exception as exc:
        return response(500, {
            "message": "Error interno",
            "error": str(exc),
        })