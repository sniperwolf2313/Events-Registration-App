import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.client("dynamodb")
sqs = boto3.client("sqs")

EVENTS_TABLE = os.environ["EVENTS_TABLE"]
REGISTRATIONS_TABLE = os.environ["REGISTRATIONS_TABLE"]
NOTIFICATIONS_QUEUE_URL = os.environ["NOTIFICATIONS_QUEUE_URL"]


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body, ensure_ascii=False)
    }


def handler(event, context):
    try:
        body = event.get("body")

        if isinstance(body, str):
            body = json.loads(body)
        elif body is None:
            body = event

        required_fields = [
            "eventId",
            "userId",
            "correo",
            "nombreCompleto"
        ]

        missing = [field for field in required_fields if field not in body]

        if missing:
            return response(400, {
                "message": "Faltan campos obligatorios",
                "missingFields": missing
            })

        event_id = body["eventId"]
        user_id = body["userId"]
        registration_date = datetime.now(timezone.utc).isoformat()

        status = "inscrito"
        attendance_status = "pendiente"
        event_status_key = f"{event_id}#{status}"

        dynamodb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": EVENTS_TABLE,
                        "Key": {
                            "eventId": {"S": event_id}
                        },
                        "UpdateExpression": "SET availableSlots = availableSlots - :one",
                        "ConditionExpression": "attribute_exists(eventId) AND availableSlots > :zero AND #status = :open",
                        "ExpressionAttributeNames": {
                            "#status": "status"
                        },
                        "ExpressionAttributeValues": {
                            ":one": {"N": "1"},
                            ":zero": {"N": "0"},
                            ":open": {"S": "ACTIVE"}
                        }
                    }
                },
                {
                    "Put": {
                        "TableName": REGISTRATIONS_TABLE,
                        "Item": {
                            "eventId": {"S": event_id},
                            "userId": {"S": user_id},
                            "registrationDate": {"S": registration_date},
                            "status": {"S": status},
                            "attendanceStatus": {"S": attendance_status},
                            "eventStatusKey": {"S": event_status_key},
                            "correo": {"S": body["correo"]},
                            "nombreCompleto": {"S": body["nombreCompleto"]}
                        },
                        "ConditionExpression": "attribute_not_exists(eventId) AND attribute_not_exists(userId)"
                    }
                }
            ]
        )

        event_response = dynamodb.get_item(
            TableName=EVENTS_TABLE,
            Key={
                "eventId": {"S": event_id}
            }
        )

        event_item = event_response.get("Item", {})

        event_title = event_item.get("title", {}).get("S", "Evento")
        start_date = event_item.get("startDate", {}).get("S", "")

        notification_message = {
            "type": "registro",
            "recipientEmail": body["correo"],
            "nombre": body["nombreCompleto"],
            "titulo": event_title,
            "eventId": event_id,
            "userId": user_id,
            "fechaInicio": start_date,
            "horaInicio": start_date,
            "createdAt": registration_date
        }

        sqs.send_message(
            QueueUrl=NOTIFICATIONS_QUEUE_URL,
            MessageBody=json.dumps(notification_message, ensure_ascii=False)
        )

        return response(201, {
            "message": "Inscripción registrada correctamente",
            "data": {
                "eventId": event_id,
                "userId": user_id,
                "registrationDate": registration_date,
                "status": status,
                "attendanceStatus": attendance_status,
                "eventStatusKey": event_status_key,
                "correo": body["correo"],
                "nombreCompleto": body["nombreCompleto"],
                "tituloEvento": event_title,
                "fechaInicio": start_date
            }
        })

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "TransactionCanceledException":
            reasons = e.response.get("CancellationReasons", [])

            if len(reasons) > 0 and reasons[0].get("Code") == "ConditionalCheckFailed":
                return response(400, {
                    "message": "El evento no existe, está cerrado o no tiene cupos disponibles."
                })

            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                return response(409, {
                    "message": "El usuario ya está inscrito en este evento."
                })

            return response(409, {
                "message": "No se pudo completar la inscripción."
            })

        return response(500, {
            "message": "Error al registrar la inscripción",
            "error": str(e)
        })

    except Exception as e:
        return response(500, {
            "message": "Error interno",
            "error": str(e)
        })