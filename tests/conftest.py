import os

# pygame 을 쓰는 테스트가 실제 창을 띄우지 않게 한다.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
