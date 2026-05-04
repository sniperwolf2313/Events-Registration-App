import json

def handler(event, context):
    return {
        "statusCode": 501,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps({
            "message": "Dummy lambda deployed."
        })
    }
