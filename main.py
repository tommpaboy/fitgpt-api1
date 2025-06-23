{
  "openapi": "3.0.1",
  "info": {
    "title": "FitGPT API",
    "description": "API för att hämta och analysera Fitbit-data i GPT, inklusive träning, sömn, puls och kost.",
    "version": "1.0.0"
  },
  "servers": [
    {
      "url": "https://fitgpt-2364.onrender.com"
    }
  ],
  "paths": {
    "/data": {
      "get": {
        "summary": "Hämta sammanfattad Fitbit-data för valfritt antal dagar",
        "operationId": "getCombinedFitbitData",
        "parameters": [
          {
            "name": "days",
            "in": "query",
            "required": False,
            "schema": {
              "type": "integer",
              "default": 1,
              "minimum": 1,
              "maximum": 30
            },
            "description": "Antal dagar att hämta data för (standard: 1)"
          }
        ],
        "responses": {
          "200": {
            "description": "Returnerar sammanfattad Fitbit-data",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object",
                  "properties": {
                    "from": { "type": "string", "format": "date" },
                    "to": { "type": "string", "format": "date" },
                    "steps": { "type": "object" },
                    "calories": { "type": "object" },
                    "sleep": { "type": "object" },
                    "heart": { "type": "object" }
                  }
                }
              }
            }
          }
        }
      }
    },
    "/data/extended": {
      "get": {
        "summary": "Hämta detaljerad Fitbit-data för ett specifikt datum eller ett antal dagar",
        "operationId": "getExtendedFitbitData",
        "parameters": [
          {
            "name": "days",
            "in": "query",
            "required": False,
            "schema": {
              "type": "integer",
              "default": 1,
              "minimum": 1,
              "maximum": 30
            }
          },
          {
            "name": "target_date",
            "in": "query",
            "required": False,
            "schema": {
              "type": "string",
              "format": "date"
            },
            "description": "Datum att hämta data för (format: YYYY-MM-DD)"
          }
        ],
        "responses": {
          "200": {
            "description": "Returnerar detaljerad Fitbit-data",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object"
                }
              }
            }
          }
        }
      }
    },
    "/data/steps": {
      "get": {
        "summary": "Hämta stegdata för ett visst datum",
        "operationId": "getSteps",
        "parameters": [
          {
            "name": "date",
            "in": "query",
            "required": True,
            "schema": { "type": "string", "format": "date" }
          }
        ],
        "responses": {
          "200": {
            "description": "Stegdata",
            "content": {
              "application/json": { "schema": { "type": "object" } }
            }
          }
        }
      }
    },
    "/data/sleep": {
      "get": {
        "summary": "Hämta sömndata för ett visst datum",
        "operationId": "getSleep",
        "parameters": [
          {
            "name": "date",
            "in": "query",
            "required": True,
            "schema": { "type": "string", "format": "date" }
          }
        ],
        "responses": {
          "200": {
            "description": "Sömndata",
            "content": {
              "application/json": { "schema": { "type": "object" } }
            }
          }
        }
      }
    },
    "/data/calories": {
      "get": {
        "summary": "Hämta kaloridata för ett visst datum",
        "operationId": "getCalories",
        "parameters": [
          {
            "name": "date",
            "in": "query",
            "required": True,
            "schema": { "type": "string", "format": "date" }
          }
        ],
        "responses": {
          "200": {
            "description": "Kaloridata",
            "content": {
              "application/json": { "schema": { "type": "object" } }
            }
          }
        }
      }
    },
    "/data/heart": {
      "get": {
        "summary": "Hämta hjärtdata för ett visst datum",
        "operationId": "getHeart",
        "parameters": [
          {
            "name": "date",
            "in": "query",
            "required": True,
            "schema": { "type": "string", "format": "date" }
          }
        ],
        "responses": {
          "200": {
            "description": "Hjärtdata",
            "content": {
              "application/json": { "schema": { "type": "object" } }
            }
          }
        }
      }
    }
  }
}
