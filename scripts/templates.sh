#!/usr/bin/env bash
set -euo pipefail

: "${AWS_REGION:=us-east-1}"
: "${TEMPLATES_TABLE:=event-manager-dev-notification-templates}"

OUTPUT_FILE="/tmp/event-manager-notification-templates.json"

export TEMPLATES_TABLE

python3 <<'PY' > "$OUTPUT_FILE"
import json
import os
from datetime import datetime, timezone

table_name = os.environ["TEMPLATES_TABLE"]
now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

BASE_STYLE = """
<style>
  body {
    margin: 0;
    padding: 0;
    background-color: #f4f7fb;
    font-family: Arial, Helvetica, sans-serif;
    color: #1f2937;
  }
  .wrapper {
    width: 100%;
    padding: 32px 0;
    background-color: #f4f7fb;
  }
  .container {
    max-width: 640px;
    margin: 0 auto;
    background-color: #ffffff;
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  }
  .header {
    background: linear-gradient(135deg, #1e3a8a, #2563eb);
    padding: 32px 28px;
    color: #ffffff;
    text-align: center;
  }
  .header h1 {
    margin: 0;
    font-size: 26px;
    line-height: 1.25;
  }
  .header p {
    margin: 10px 0 0;
    font-size: 15px;
    opacity: 0.92;
  }
  .content {
    padding: 32px 28px;
  }
  .content h2 {
    margin: 0 0 16px;
    font-size: 22px;
    color: #111827;
  }
  .content p {
    margin: 0 0 16px;
    font-size: 15px;
    line-height: 1.7;
  }
  .event-card {
    margin: 24px 0;
    padding: 20px;
    background-color: #f8fafc;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
  }
  .event-card h3 {
    margin: 0 0 14px;
    font-size: 18px;
    color: #1e3a8a;
  }
  .detail {
    margin: 8px 0;
    font-size: 14px;
    color: #374151;
  }
  .label {
    font-weight: 700;
    color: #111827;
  }
  .button {
    display: inline-block;
    margin-top: 16px;
    padding: 12px 18px;
    border-radius: 10px;
    background-color: #2563eb;
    color: #ffffff !important;
    text-decoration: none;
    font-weight: 700;
    font-size: 14px;
  }
  .warning {
    background-color: #fff7ed;
    border: 1px solid #fed7aa;
    color: #9a3412;
    padding: 16px;
    border-radius: 12px;
    margin: 20px 0;
  }
  .success {
    background-color: #ecfdf5;
    border: 1px solid #bbf7d0;
    color: #166534;
    padding: 16px;
    border-radius: 12px;
    margin: 20px 0;
  }
  .footer {
    padding: 22px 28px;
    background-color: #f8fafc;
    color: #6b7280;
    font-size: 12px;
    text-align: center;
  }
</style>
"""

def html(title, subtitle, body):
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  {BASE_STYLE}
</head>
<body>
  <div class="wrapper">
    <div class="container">
      <div class="header">
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div class="content">
        {body}
      </div>
      <div class="footer">
        Event Manager · Notificación generada automáticamente · {{generatedAt}}
      </div>
    </div>
  </div>
</body>
</html>"""

def item(template_type, subject, text_body, html_body, description):
    return {
        "PutRequest": {
            "Item": {
                "templateId": {"S": f"TPL#{template_type}"},
                "templateType": {"S": template_type},
                "status": {"S": "ACTIVE"},
                "templateTypeStatus": {"S": f"{template_type}#ACTIVE"},
                "channel": {"S": "EMAIL"},
                "subject": {"S": subject},
                "textBody": {"S": text_body},
                "htmlBody": {"S": html_body},
                "description": {"S": description},
                "createdAt": {"S": now},
                "updatedAt": {"S": now}
            }
        }
    }

templates = []

templates.append(item(
    "REGISTRATION_CONFIRMATION",
    "Confirmación de registro - {title}",
    """Hola {fullName},

Tu registro al evento {title} fue confirmado.

Fecha: {startDate}
Ubicación: {location}
ID del evento: {eventId}

Gracias por registrarte.""",
    html(
        "Registro confirmado",
        "Tu inscripción fue realizada correctamente.",
        """
        <h2>Hola {fullName},</h2>
        <div class="success">
          Tu registro fue confirmado exitosamente.
        </div>
        <p>Te esperamos en el evento. Guarda esta información para consultarla más adelante.</p>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        """
    ),
    "Confirmation email sent after attendee registration."
))

templates.append(item(
    "REGISTRATION_CANCELLED",
    "Cancelación de registro - {title}",
    """Hola {fullName},

Tu registro al evento {title} fue cancelado correctamente.

Fecha: {startDate}
Ubicación: {location}
ID del evento: {eventId}""",
    html(
        "Registro cancelado",
        "Tu inscripción fue cancelada correctamente.",
        """
        <h2>Hola {fullName},</h2>
        <p>Confirmamos que tu registro al evento fue cancelado.</p>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        """
    ),
    "Email sent when an attendee cancels their registration."
))

templates.append(item(
    "EVENT_REMINDER_24H",
    "Recordatorio: {title} inicia en 24 horas",
    """Hola {fullName},

Te recordamos que el evento {title} inicia en 24 horas.

Fecha: {startDate}
Ubicación: {location}
ID del evento: {eventId}""",
    html(
        "Recordatorio 24 horas",
        "Tu evento está cada vez más cerca.",
        """
        <h2>Hola {fullName},</h2>
        <p>Te recordamos que estás registrado en el siguiente evento, que inicia en aproximadamente 24 horas.</p>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        <p>Revisa la hora y ubicación para llegar a tiempo.</p>
        """
    ),
    "Reminder sent 24 hours before event start."
))

templates.append(item(
    "EVENT_REMINDER_12H",
    "Recordatorio: {title} inicia en 12 horas",
    """Hola {fullName},

Te recordamos que el evento {title} inicia en 12 horas.

Fecha: {startDate}
Ubicación: {location}
ID del evento: {eventId}""",
    html(
        "Recordatorio 12 horas",
        "Tu evento inicia pronto.",
        """
        <h2>Hola {fullName},</h2>
        <p>Este es un recordatorio de que el evento al que estás registrado inicia en aproximadamente 12 horas.</p>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        <p>Te recomendamos revisar cualquier instrucción previa al inicio del evento.</p>
        """
    ),
    "Reminder sent 12 hours before event start."
))

templates.append(item(
    "EVENT_THANK_YOU",
    "Gracias por participar en {title}",
    """Hola {fullName},

Gracias por participar en el evento {title}.

Esperamos verte en próximos eventos.

ID del evento: {eventId}""",
    html(
        "Gracias por participar",
        "Esperamos que hayas disfrutado el evento.",
        """
        <h2>Hola {fullName},</h2>
        <p>Gracias por participar en nuestro evento.</p>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
        </div>
        <p>Tu participación es muy valiosa. Esperamos verte nuevamente en próximos eventos.</p>
        """
    ),
    "Thank you email sent after event completion."
))

templates.append(item(
    "EVENT_CANCELLED",
    "Evento cancelado - {title}",
    """Hola {fullName},

Te informamos que el evento {title} fue cancelado.

Fecha original: {startDate}
Ubicación: {location}
ID del evento: {eventId}""",
    html(
        "Evento cancelado",
        "Información importante sobre tu evento.",
        """
        <h2>Hola {fullName},</h2>
        <div class="warning">
          Te informamos que el evento al que estabas registrado fue cancelado.
        </div>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha original:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        <p>Lamentamos los inconvenientes ocasionados.</p>
        """
    ),
    "Email sent to registered attendees when an event is cancelled."
))

templates.append(item(
    "EVENT_UPDATED",
    "Actualización del evento - {title}",
    """Hola {fullName},

El evento {title} fue actualizado.

{customMessage}

Fecha: {startDate}
Ubicación: {location}
ID del evento: {eventId}""",
    html(
        "Evento actualizado",
        "Hay cambios importantes en un evento al que estás registrado.",
        """
        <h2>Hola {fullName},</h2>
        <p>El evento al que estás registrado tuvo una actualización.</p>
        <div class="warning">
          {customMessage}
        </div>
        <div class="event-card">
          <h3>{title}</h3>
          <div class="detail"><span class="label">Fecha:</span> {startDate}</div>
          <div class="detail"><span class="label">Ubicación:</span> {location}</div>
          <div class="detail"><span class="label">ID del evento:</span> {eventId}</div>
        </div>
        """
    ),
    "Email sent when organizer updates relevant event details."
))

templates.append(item(
    "REPORT_READY",
    "Reporte listo - {reportId}",
    """Hola,

Tu reporte {reportId} ya está disponible.

Descarga: {downloadUrl}

Este enlace puede expirar.""",
    html(
        "Reporte listo",
        "Tu reporte administrativo fue generado correctamente.",
        """
        <h2>Tu reporte está disponible</h2>
        <p>El reporte <strong>{reportId}</strong> ya fue generado y está listo para descarga.</p>
        <a class="button" href="{downloadUrl}">Descargar reporte</a>
        <div class="event-card">
          <div class="detail"><span class="label">Reporte:</span> {reportId}</div>
          <div class="detail"><span class="label">Expiración del enlace:</span> {expiresInSeconds} segundos</div>
        </div>
        <p>Si el enlace expira, solicita nuevamente el reporte desde la plataforma.</p>
        """
    ),
    "Email sent to organizer when a report is ready."
))

print(json.dumps({table_name: templates}, ensure_ascii=False, indent=2))
PY

aws dynamodb batch-write-item \
  --request-items "file://${OUTPUT_FILE}" \
  --region "${AWS_REGION}"

echo "Templates uploaded to ${TEMPLATES_TABLE} in ${AWS_REGION}"