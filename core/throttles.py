from rest_framework.throttling import AnonRateThrottle


class TrackShipmentThrottle(AnonRateThrottle):
    scope = 'track_shipment'


class AIChatThrottle(AnonRateThrottle):
    scope = 'ai_chat'
