"""One lightweight encoding preset for concert review media, never final renders."""

PREVIEW_PROFILE = "540p-h264-550k-aac-80k-v1"
PREVIEW_SCALE = "scale=-2:trunc(min(540\\,ih)/2)*2"


def preview_encoding_args() -> list[str]:
    return [
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "550k", "-maxrate", "700k", "-bufsize", "1400k",
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-fps_mode", "vfr",
        "-c:a", "aac", "-b:a", "80k", "-ac", "2",
    ]
