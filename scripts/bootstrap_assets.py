import wave
import math
import struct
import random
import os

def create_wav(filepath, duration, sample_rate, generator_func):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    num_samples = int(duration * sample_rate)
    with wave.open(filepath, 'w') as wav_file:
        wav_file.setnchannels(1)  # 单声道
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        
        for i in range(num_samples):
            t = float(i) / sample_rate
            sample_val = generator_func(t, duration, i)
            # 限制幅度防爆音
            sample_val = max(-1.0, min(1.0, sample_val))
            packed_val = struct.pack('<h', int(sample_val * 32767.0))
            wav_file.writeframes(packed_val)

def generate_soft_typing(t, duration, i):
    # 柔和的机械/薄膜按键打字音：降低频率(280Hz~420Hz)，低通阻尼指数衰减，温暖柔和，彻底告别尖锐高频滴滴声
    period = 0.18
    local_t = t % period
    if local_t < 0.08:
        decay = math.exp(-local_t * 60.0)
        freq = 340.0 + (random.random() - 0.5) * 60.0
        # 柔和正弦基波 + 微弱次泛音，没有刺激的高频噪音
        thump = math.sin(2 * math.pi * freq * local_t) * 0.4
        body = math.sin(2 * math.pi * (freq * 0.5) * local_t) * 0.2
        return (thump + body) * decay * 0.6
    return 0.0

def generate_whoosh(t, duration, i):
    # 柔和空气感转场轻扫音
    p = t / duration
    env = math.sin(p * math.pi) ** 2
    f = 160.0 + 260.0 * math.sin(p * math.pi)
    noise = (random.random() * 2.0 - 1.0) * 0.15
    tone = math.sin(2 * math.pi * f * t) * 0.25
    return (tone + noise) * env * 0.5

def generate_ding(t, duration, i):
    # 温暖空灵提示轻音 (440Hz A4 + 659Hz E5)
    decay = math.exp(-t * 5.0)
    tone1 = math.sin(2 * math.pi * 587.33 * t) # D5
    tone2 = math.sin(2 * math.pi * 880.0 * t) * 0.4 # A5
    return (tone1 + tone2) * 0.4 * decay * 0.5

def generate_glitch(t, duration, i):
    # 柔和电音过渡小音效
    decay = math.exp(-t * 8.0)
    f = 350.0 + math.sin(2 * math.pi * 20.0 * t) * 80.0
    val = math.sin(2 * math.pi * f * t)
    return val * 0.3 * decay

def generate_bgm(t, duration, i):
    # 极客低频和弦背景底噪 (110Hz + 164.8Hz + 220Hz)
    w1 = math.sin(2 * math.pi * 110.0 * t)
    w2 = math.sin(2 * math.pi * 164.81 * t) * 0.8
    w3 = math.sin(2 * math.pi * 220.0 * t) * 0.6
    return (w1 + w2 + w3) * 0.08

def main():
    sfx_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "renderer", "public", "sfx"))
    print(f"Regenerating soft audio assets in {sfx_dir}...")
    sample_rate = 44100

    create_wav(os.path.join(sfx_dir, "typing.wav"), 0.8, sample_rate, generate_soft_typing)
    create_wav(os.path.join(sfx_dir, "whoosh.wav"), 0.4, sample_rate, generate_whoosh)
    create_wav(os.path.join(sfx_dir, "ding.wav"), 0.6, sample_rate, generate_ding)
    create_wav(os.path.join(sfx_dir, "glitch.wav"), 0.3, sample_rate, generate_glitch)
    create_wav(os.path.join(sfx_dir, "bgm_loop.wav"), 5.0, sample_rate, generate_bgm)
    print("Soft audio assets regenerated successfully!")

if __name__ == "__main__":
    main()
