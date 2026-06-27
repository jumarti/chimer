ffmpeg -f pulse   -i "$(pactl info | awk -F': ' '/Default Sink/ {print $2}').monitor"   -acodec pcm_s16le   -ac 2   -ar 44100   webpage_audio.wav
