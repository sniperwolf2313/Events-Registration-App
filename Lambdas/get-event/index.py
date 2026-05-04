import base64
import json
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]

GSI_STATUS_START_DATE = os.environ.get("GSI_STATUS_START_DATE", "GSI1_StatusStartDate")
GSI_CREATED_BY_CREATED_AT = os.environ.get("GSI_CREATED_BY_CREATED_AT", "GSI2_CreatedByCreatedAt")

events_table = dynamodb.Table(EVENTS_TABLE)

STATUS_ACTIVE = "ACTIVE"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_FINISHED = "FINISHED"
STATUS_CANCELLED = "CANCELLED"

VALID_STATUSES = {
    STATUS_ACTIVE,
    STATUS_IN_PROGRESS,
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


def get_auth_context(event):
    claims = get_claims(event)

    if not claims:
        return None, response(401, {
            "message": "Unauthorized",
            "reason": "No JWT claims found. Use API Gateway with a valid Cognito token.",
        })

    groups = normalize_groups(claims.get("cognito:groups"))

    return {
        "claims": claims,
        "groups": groups,
        "userId": claims.get("sub"),
        "email": claims.get("email"),
        "isOrganizer": "organizers" in groups,
        "isAttendee": "attendees" in groups,
    }, None


def query_params(event):
    return event.get("queryStringParameters") or {}


def path_params(event):
    return event.get("pathParameters") or {}

def parse_limit(params):
    raw_limit = params.get("limit")

    if not raw_limit:
        return 50

    try:
        limit = int(raw_limit)
    except ValueError:
        return 50

    if limit <= 0:
        return 50

    return min(limit, 100)



def sanitize_event_for_attendee(item):
    """
    Los asistentes no necesitan ver detalles internos como schedules,
    schedulingDetails, createdByEmail o datos administrativos.
    """
    allowed_fields = [
        "eventId",
        "title",
        "description",
        "location",
        "startDate",
        "endDate",
        "durationMinutes",
        "capacity",
        "availableSlots",
        "status",
        "statusLabel",
        "registrationOpen",
    ]

    return {
        field: item.get(field)
        for field in allowed_fields
        if field in item
    }


def sanitize_events(items, auth_context):
    clean_items = [decimal_to_native(item) for item in items]

    if auth_context["isOrganizer"]:
        return clean_items

    return [
        sanitize_event_for_attendee(item)
        for item in clean_items
    ]


def get_event_by_id(event_id, auth_context):
    result = events_table.get_item(
        Key={
            "eventId": event_id
        }
    )

    item = result.get("Item")

    if not item:
        return response(404, {
            "message": "Evento no encontrado",
            "eventId": event_id,
        })

    item = decimal_to_native(item)

    if auth_context["isOrganizer"]:
        return response(200, {
            "event": item,
        })

    if item.get("status") != STATUS_ACTIVE or item.get("registrationOpen") is False:
        return response(404, {
            "message": "Evento no encontrado o no disponible para asistentes",
            "eventId": event_id,
        })

    return response(200, {
        "event": sanitize_event_for_attendee(item),
    })


def query_events_by_status(status, limit):
    params = {
        "IndexName": GSI_STATUS_START_DATE,
        "KeyConditionExpression": Key("status").eq(status),
        "Limit": limit,
        "ScanIndexForward": True,
    }

    result = events_table.query(**params)

    return {
        "items": result.get("Items", []),
    }


def query_events_by_creator(created_by, limit):
    params = {
        "IndexName": GSI_CREATED_BY_CREATED_AT,
        "KeyConditionExpression": Key("createdBy").eq(created_by),
        "Limit": limit,
        "ScanIndexForward": False,
    }

    result = events_table.query(**params)

    return {
        "items": result.get("Items", []),
    }


def list_all_events_for_organizer(limit_per_status):
    """
    Evita Scan. Consulta por status usando GSI1.
    Para el MVP es suficiente y usa los índices existentes.
    """
    all_items = []
    errors = []

    for status in [
        STATUS_ACTIVE,
        STATUS_IN_PROGRESS,
        STATUS_FINISHED,
        STATUS_CANCELLED,
    ]:
        try:
            result = query_events_by_status(
                status=status,
                limit=limit_per_status,
            )
            all_items.extend(result["items"])
        except ClientError as exc:
            errors.append({
                "status": status,
                "error": str(exc),
            })

    all_items.sort(key=lambda item: item.get("startDate", ""))

    return {
        "items": all_items,
        "errors": errors,
    }


def list_events(event, auth_context):
    params = query_params(event)

    limit = parse_limit(params)

    requested_status = params.get("status")
    mine = str(params.get("mine", "false")).lower() == "true"
    include_history = str(params.get("includeHistory", "false")).lower() == "true"

    # Asistentes: siempre ven solo eventos activos.
    if not auth_context["isOrganizer"]:
        result = query_events_by_status(
            status=STATUS_ACTIVE,
            limit=limit,
        )

        items = [
            item
            for item in result["items"]
            if item.get("registrationOpen") is not False
        ]

        return response(200, {
            "items": sanitize_events(items, auth_context),
            "count": len(items),
            "visibility": "attendee_active_events_only",
        })

    # Organizadores: pueden pedir solo sus eventos.
    if mine:
        result = query_events_by_creator(
            created_by=auth_context["userId"],
            limit=limit,
        )

        return response(200, {
            "items": sanitize_events(result["items"], auth_context),
            "count": len(result["items"]),
            "visibility": "organizer_own_events",
        })

    # Organizadores: pueden filtrar por status.
    if requested_status:
        result = query_events_by_status(
            status=requested_status,
            limit=limit,
        )

        return response(200, {
            "items": sanitize_events(result["items"], auth_context),
            "count": len(result["items"]),
            "visibility": "organizer_status_filter",
            "status": requested_status,
        })


    result = list_all_events_for_organizer(limit_per_status=limit)

    return response(200, {
        "items": sanitize_events(result["items"], auth_context),
        "count": len(result["items"]),
        "visibility": "organizer_full_history",
        "errors": result["errors"],
    })


def handler(event, context):
    try:
        auth_context, auth_error = get_auth_context(event)

        if auth_error:
            return auth_error

        params = path_params(event)
        event_id = params.get("eventId")

        if event_id:
            return get_event_by_id(event_id, auth_context)

        return list_events(event, auth_context)

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        return response(500, {
            "message": "Error al consultar eventos",
            "awsErrorCode": error_code,
            "error": str(exc),
        })

    except Exception as exc:
        return response(500, {
            "message": "Error interno",
            "error": str(exc),
        })