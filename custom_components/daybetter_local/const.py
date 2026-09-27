"""Constants for the DayBetter Local integration."""

DOMAIN = "daybetter_local"

# Protocolo local oficial de DayBetter (daybetter-local-api)
SCAN_PORT = 6281
COMMAND_PORT = 6283

SCAN_TIMEOUT = 6.0
UPDATE_INTERVAL = 5
UNAVAILABLE_AFTER = 16  # segundos sin respuesta -> entidad no disponible
