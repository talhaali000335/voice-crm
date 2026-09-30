from django.urls import path

from .views import SpeakView, VoiceView

urlpatterns = [
    path("", VoiceView.as_view(), name="voice"),
    path("speak/", SpeakView.as_view(), name="voice-speak"),
]
