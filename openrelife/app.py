from threading import Thread
import os
import base64

import numpy as np
from flask import Flask, render_template_string, request, send_from_directory, jsonify
from jinja2 import BaseLoader
from PIL import Image

from openrelife.config import appdata_folder, screenshots_path
from openrelife.database import create_db, get_timestamps, update_ai_ocr, delete_entries, get_entry_by_timestamp, get_entries_light, get_entries_metadata, get_new_timestamps, search_entries_streaming, build_snippet, DEFAULT_MIN_SIMILARITY
from openrelife.nlp import get_embedding
from openrelife.screenshot import (
    record_screenshots_thread,
    ocr_worker_thread,
    get_recording_paused,
    set_recording_paused,
    set_viewer_open,
    get_screenshot_interval,
    set_screenshot_interval,
    get_screenshot_quality,
    set_screenshot_quality,
    get_skip_incognito_recording,
    set_skip_incognito_recording,
    get_ocr_cooldown,
    set_ocr_cooldown,
    get_ocr_compute_mode,
    set_ocr_compute_mode,
    get_use_apple_vision,
    set_use_apple_vision,
    ocr_one_frame,
)
from openrelife.apple_vision_ocr import is_apple_vision_available
from openrelife.utils import human_readable_time, timestamp_to_human_readable
from openrelife.ai_ocr import get_ai_provider

app = Flask(__name__)

def load_settings():
    settings_path = os.path.join(appdata_folder, "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                import json
                settings = json.load(f)
                if 'screenshot_interval' in settings:
                    set_screenshot_interval(int(settings['screenshot_interval']))
                if 'screenshot_quality' in settings:
                    set_screenshot_quality(settings['screenshot_quality'])
                if 'skip_incognito' in settings:
                    set_skip_incognito_recording(bool(settings['skip_incognito']))
                if 'ocr_cooldown' in settings:
                    set_ocr_cooldown(int(settings['ocr_cooldown']))
                if 'ocr_compute_mode' in settings:
                    set_ocr_compute_mode(settings['ocr_compute_mode'])
                if 'use_apple_vision' in settings:
                    set_use_apple_vision(bool(settings['use_apple_vision']))
                elif is_apple_vision_available():
                    # First-run default: enable on supported platforms
                    set_use_apple_vision(True)
                # else: leave default False (already set in screenshot.py)
        except Exception as e:
            print(f"Error loading settings: {e}")

app.jinja_env.filters["human_readable_time"] = human_readable_time
app.jinja_env.filters["timestamp_to_human_readable"] = timestamp_to_human_readable

base_template = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenReLife</title>
  <!-- Bootstrap CSS -->
  <link href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.3.0/font/bootstrap-icons.css">
  <style>
    * {
      overscroll-behavior-x: none;
      overscroll-behavior-y: contain;
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }
    body, html {
      overscroll-behavior-x: none;
      height: 100vh;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #000;
      color: #fff;
    }
    
    /* Fullscreen layout */
    .fullscreen-container {
      width: 100vw;
      height: 100vh;
      display: flex;
      flex-direction: column;
      position: relative;
    }
    
    /* Search bar - top right */
    .search-container {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 1000;
    }
    .search-input {
      width: 400px;
      padding: 12px 20px;
      padding-right: 45px;
      border-radius: 24px;
      border: 1px solid rgba(255, 255, 255, 0.2);
      background: rgba(30, 30, 30, 0.9);
      backdrop-filter: blur(20px);
      color: #fff;
      font-size: 15px;
      transition: all 0.2s;
    }
    .search-input:focus {
      outline: none;
      border-color: rgba(0, 123, 255, 0.6);
      background: rgba(40, 40, 40, 0.95);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }
    .search-icon {
      position: absolute;
      right: 15px;
      top: 50%;
      transform: translateY(-50%);
      color: rgba(255, 255, 255, 0.5);
      pointer-events: none;
    }
    
    /* Search results modal */
    .search-results-modal {
      position: fixed;
      top: 80px;
      right: 20px;
      width: 800px;
      max-height: calc(100vh - 120px);
      background: rgba(30, 30, 30, 0.98);
      backdrop-filter: blur(40px);
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
      padding: 20px;
      overflow-y: auto;
      display: none;
      z-index: 999;
    }
    .search-results-modal.show {
      display: block;
    }
    .search-results-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 16px;
    }
    .search-result-card {
      background: rgba(50, 50, 50, 0.6);
      border-radius: 12px;
      overflow: hidden;
      cursor: pointer;
      transition: all 0.2s;
      border: 2px solid transparent;
    }
    .search-result-card:hover {
      transform: scale(1.05);
      border-color: rgba(0, 123, 255, 0.6);
      box-shadow: 0 8px 24px rgba(0, 123, 255, 0.3);
    }
    .search-result-card img {
      width: 100%;
      height: 120px;
      object-fit: cover;
    }
    .search-result-time {
      padding: 8px 12px;
      font-size: 11px;
      color: rgba(255, 255, 255, 0.6);
      text-align: center;
    }
    
    /* Main screenshot area */
    .screenshot-area {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 60px 40px 120px;
      position: relative;
    }
    .screenshot-wrapper {
      position: relative;
      max-width: 100%;
      max-height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .screenshot-wrapper img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      border-radius: 8px;
      box-shadow: 0 20px 80px rgba(0, 0, 0, 0.5);
      user-select: none;
      -webkit-user-select: none;
      -webkit-user-drag: none;
    }
    
    /* Text overlay icons */
    .text-block-icon {
      position: absolute;
      background: rgba(0, 123, 255, 0.15);
      color: rgba(255, 255, 255, 0.4);
      border-radius: 50%;
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 16px;
      transition: all 0.2s;
      pointer-events: auto;
      z-index: 10;
    }
    .text-block-icon:hover {
      background: rgba(0, 123, 255, 0.9);
      color: white;
      transform: scale(1.2);
      box-shadow: 0 4px 12px rgba(0, 123, 255, 0.4);
    }
    
    /* Timeline - bottom center */
    .timeline-container {
      position: fixed;
      bottom: 30px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 1000;
    }
    .timeline-pill {
      background: rgba(30, 30, 30, 0.95);
      backdrop-filter: blur(40px);
      border-radius: 32px;
      padding: 16px 32px;
      border: 1px solid rgba(255, 255, 255, 0.15);
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      min-width: 400px;
    }
    .timeline-date {
      font-size: 14px;
      font-weight: 500;
      color: rgba(255, 255, 255, 0.9);
      letter-spacing: 0.3px;
    }
    .timeline-slider {
      width: 100%;
      height: 4px;
      -webkit-appearance: none;
      appearance: none;
      background: rgba(255, 255, 255, 0.2);
      border-radius: 2px;
      outline: none;
    }
    .timeline-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      appearance: none;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: #007bff;
      cursor: pointer;
      box-shadow: 0 2px 8px rgba(0, 123, 255, 0.4);
    }
    .timeline-slider::-moz-range-thumb {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: #007bff;
      cursor: pointer;
      border: none;
      box-shadow: 0 2px 8px rgba(0, 123, 255, 0.4);
    }
    
    /* Text popup */
      z-index: 1100;
      background: white;
      border-radius: 50%;
      width: 48px;
      height: 48px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
      cursor: pointer;
      transition: all 0.2s;
    }
    .home-icon:hover {
      transform: scale(1.1);
      box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    .toggle-sidebar-btn {
      position: fixed;
      top: 75px;
      right: 15px;
      z-index: 1100;
      background: white;
      border-radius: 50%;
      width: 40px;
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 10px rgba(0,0,0,0.2);
      cursor: pointer;
      transition: all 0.2s;
      border: 2px solid #007bff;
    }
    .toggle-sidebar-btn:hover {
      transform: scale(1.1);
      background: #007bff;
      color: white;
    }
    .text-popup {
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      background: white;
      border-radius: 8px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
      max-width: 600px;
      max-height: 80vh;
      overflow: hidden;
      z-index: 1000;
      display: none;
    }
    .text-popup.show {
      display: block;
    }
    .text-popup-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0,0,0,0.5);
      z-index: 999;
      display: none;
    }
    .text-popup-overlay.show {
      display: block;
    }
    .text-popup-header {
      padding: 15px;
      border-bottom: 1px solid #dee2e6;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .text-popup-body {
      padding: 15px;
      max-height: 60vh;
      overflow-y: auto;
    }
    .text-popup-footer {
      padding: 15px;
      border-top: 1px solid #dee2e6;
      display: flex;
      justify-content: flex-end;
      gap: 10px;
    }
  </style>
</head>
<body>
<a href="/" class="home-icon" title="Home">
  <i class="bi bi-house-fill" style="font-size: 1.5rem; color: #007bff;"></i>
</a>
<a href="/timeline-v2" class="fullscreen-link" title="Fullscreen View">
  <i class="bi bi-fullscreen"></i>
</a>
<nav class="navbar navbar-light bg-light">
  <div class="container">
    <form class="form-inline my-2 my-lg-0 w-100 d-flex" action="/search" method="get">
      <input class="form-control flex-grow-1 mr-sm-2" type="search" name="q" placeholder="Search" aria-label="Search">
      <button class="btn btn-outline-secondary my-2 my-sm-0" type="submit">
        <i class="bi bi-search"></i>
      </button>
    </form>
  </div>
</nav>
{% block content %}

{% endblock %}

  <!-- Bootstrap and jQuery JS -->
  <script src="https://code.jquery.com/jquery-3.5.1.slim.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.5.3/dist/umd/popper.min.js"></script>
  <script src="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/js/bootstrap.min.js"></script>
  
</body>
</html>
"""


class StringLoader(BaseLoader):
    def get_source(self, environment, template):
        if template == "base_template":
            return base_template, None, lambda: True
        return None, None, None


app.jinja_env.loader = StringLoader()


@app.route("/")
@app.route("/timeline-v2")
def timeline_v2():
    """New Rewind.ai style interface"""
    all_timestamps = get_timestamps()
    # Optimization: Loading too many entries causes slow page rendering.
    # We limit initial load to 50 items. The user will see the most recent ones.
    # The frontend will fetch older entries on demand.
    limit = 50
    if len(all_timestamps) > limit:
        # We still need all timestamps for the slider
        partial_timestamps = all_timestamps[:limit]
        entries = get_entries_metadata(limit=limit)
    else:
        partial_timestamps = all_timestamps
        entries = get_entries_metadata()

    entries_dict = {
        entry.timestamp: {
            'id': entry.id,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'ai_text': entry.ai_text,
        }
        for entry in entries
    }
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>OpenReLife</title>
  <link href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.3.0/font/bootstrap-icons.css">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; overscroll-behavior-x: none; }
    body, html {
      height: 100vh; overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #000; color: #fff;
    }
    .fullscreen-container { width: 100vw; height: 100vh; position: relative; }
    
    /* Search bar */
    .search-container { position: fixed; top: 22px; left: 50%; right: auto; transform: translateX(-50%); z-index: 1000; }
    .search-wrapper { position: relative; }
    .search-input {
      width: min(400px, calc(100vw - 100px)); padding: 12px 45px 12px 20px; border-radius: 24px;
      border: 1px solid rgba(255,255,255,0.15); background: rgba(20,20,20,0.75);
      backdrop-filter: blur(30px); color: #fff; font-size: 15px; transition: all 0.2s;
    }
    .search-input:focus {
      outline: none; border-color: rgba(0,123,255,0.5);
      background: rgba(30,30,30,0.85); box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    .search-icon { position: absolute; right: 15px; top: 50%; transform: translateY(-50%); color: rgba(255,255,255,0.4); }
    /* Loading indicator: a light sweeping around the search bar border. */
    @property --orl-angle { syntax: "<angle>"; initial-value: 0deg; inherits: false; }
    .search-wrapper.searching::after {
      content: "";
      position: absolute; inset: -2px; border-radius: 26px;
      padding: 2.5px;  /* ring thickness */
      /* A comet of light — bright head, trailing tail — sweeping the border. */
      background: conic-gradient(from var(--orl-angle),
        rgba(13,110,253,0) 0deg, rgba(13,110,253,0) 50deg,
        rgba(77,155,255,0.45) 140deg, #4d9bff 168deg, #eaf4ff 179deg,
        rgba(13,110,253,0) 184deg, rgba(13,110,253,0) 360deg);
      -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
      -webkit-mask-composite: xor; mask-composite: exclude;
      filter: drop-shadow(0 0 4px rgba(77,155,255,0.8)) drop-shadow(0 0 9px rgba(13,110,253,0.55));
      animation: orl-border-sweep 2.4s linear infinite;
      pointer-events: none; z-index: 3;
    }
    /* One sweep, then fade out and pause before the next pass. */
    @keyframes orl-border-sweep {
      0%   { --orl-angle: 0deg;   opacity: 0; }
      5%   { opacity: 1; }
      58%  { --orl-angle: 360deg; opacity: 1; }
      66%  { --orl-angle: 360deg; opacity: 0; }
      100% { --orl-angle: 360deg; opacity: 0; }
    }
    
    /* Search results */
    .search-results {
      position: fixed; top: 80px; right: 20px; 
      width: min(850px, calc(100vw - 40px)); max-height: calc(100vh - 120px);
      background: rgba(30,30,30,0.98); backdrop-filter: blur(40px); border-radius: 16px;
      border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 20px 60px rgba(0,0,0,0.6);
      padding: 20px; overflow-y: auto; display: none; z-index: 999;
    }
    .search-results.show { display: block; }
    .results-grid {
      display: grid; 
      grid-template-columns: repeat(auto-fill, minmax(min(180px, 100%), 1fr)); 
      gap: 16px;
    }
    .result-card {
      background: rgba(50,50,50,0.6); border-radius: 12px; overflow: hidden;
      cursor: pointer; transition: all 0.2s; border: 2px solid transparent;
    }
    .result-card:hover {
      transform: scale(1.05); border-color: rgba(0,123,255,0.6);
      box-shadow: 0 8px 24px rgba(0,123,255,0.3);
    }
    /* Film-develop reveal: thumbnails fade in as they load (post-scan "alive" feel). */
    .result-card img { width: 100%; height: 120px; object-fit: cover; opacity: 0; transition: opacity 0.25s ease; }
    .result-card img.loaded { opacity: 1; }
    .result-time { padding: 8px 12px 2px; font-size: 11px; color: rgba(255,255,255,0.6); text-align: center; }
    .result-snippet {
      /* bottom spacing as MARGIN, not padding: overflow:hidden clips at the
         padding box, so a padding-bottom would let the clamped 3rd line peek. */
      padding: 0 12px; margin-bottom: 10px; font-size: 11px; line-height: 1.35; color: rgba(255,255,255,0.55);
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; word-break: break-word;
    }
    mark.search-mark { background: rgba(13,110,253,0.4); color: #eaf4ff; border-radius: 3px; padding: 0 1px; }
    /* Keyboard selection: distinct focus ring, capped scale so edge cards aren't clipped. */
    .result-card.selected {
      transform: scale(1.03); border-color: rgba(0,123,255,0.6);
      box-shadow: 0 8px 24px rgba(0,123,255,0.3);
      outline: 2px solid rgba(77,155,255,0.95); outline-offset: 2px;
    }
    .search-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
    .search-count { font-size: 13px; color: rgba(255,255,255,0.55); }
    .skeleton-card {
      background: rgba(50,50,50,0.6); border-radius: 12px; height: 172px; position: relative; overflow: hidden;
    }
    .skeleton-card::after {
      content: ""; position: absolute; inset: 0; transform: translateX(-100%);
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.07), transparent);
      animation: orl-shimmer 1.3s linear infinite;
    }
    @keyframes orl-shimmer { to { transform: translateX(100%); } }
    .load-more {
      width: 100%; margin-top: 16px; padding: 10px; border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.12); background: rgba(255,255,255,0.05);
      color: rgba(255,255,255,0.8); font-size: 13px; cursor: pointer; transition: all 0.15s;
    }
    .load-more:hover { border-color: rgba(13,110,253,0.6); background: rgba(13,110,253,0.18); }
    .load-more[disabled] { opacity: 0.5; cursor: default; }
    .search-empty, .search-error { text-align: center; padding: 30px 16px; color: rgba(255,255,255,0.55); }
    .search-retry {
      margin-top: 12px; padding: 8px 16px; border-radius: 10px; border: 1px solid rgba(13,110,253,0.6);
      background: rgba(13,110,253,0.18); color: #eaf4ff; cursor: pointer;
    }
    .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
    @media (prefers-reduced-motion: reduce) {
      .skeleton-card::after, .search-wrapper.searching::after { animation: none; }
      .result-card img { transition: none; opacity: 1; }
    }

    .clear-icon {
      position: absolute; right: 15px; top: 50%; transform: translateY(-50%);
      color: rgba(255,255,255,0.6); cursor: pointer; pointer-events: auto; z-index: 10;
      font-size: 16px;
    }
    .clear-icon:hover { color: #fff; }
    
    /* Screenshot area */
    .screenshot-area {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0;
    }
    .screenshot-wrapper { 
      position: relative; 
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .screenshot-wrapper img {
      width: 100%;
      height: 100%;
      object-fit: contain; 
      border-radius: 8px;
      box-shadow: 0 20px 80px rgba(0,0,0,0.5);
      user-select: none;
      -webkit-user-select: none;
      -webkit-user-drag: none;
    }
    .text-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); pointer-events: none; }
    
    /* Text icons */
    .text-icon {
      position: absolute; background: rgba(0,123,255,0.15); color: rgba(255,255,255,0.4);
      border-radius: 50%; width: 32px; height: 32px; display: flex; align-items: center;
      justify-content: center; cursor: pointer; transition: all 0.2s; pointer-events: auto; z-index: 10;
    }
    .text-icon:hover {
      background: rgba(0,123,255,0.9); color: white; transform: scale(1.2);
      box-shadow: 0 4px 12px rgba(0,123,255,0.4);
    }
    
    /* Timeline */
    .timeline {
      position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); z-index: 1000;
    }
    .timeline-pill {
      background: rgba(20,20,20,0.75); backdrop-filter: blur(30px); border-radius: 32px;
      padding: 16px 32px; border: 1px solid rgba(255,255,255,0.12);
      box-shadow: 0 10px 40px rgba(0,0,0,0.5); display: flex; flex-direction: column;
      align-items: center; gap: 12px; min-width: 400px; transition: all 0.3s ease;
      position: relative;
    }
    .timeline-pill.delete-mode {
      box-shadow: 0 0 0 2px rgba(220, 53, 69, 0.5), 0 10px 40px rgba(220, 53, 69, 0.3);
      border-color: rgba(220, 53, 69, 0.3);
    }
    .timeline-header {
      width: 100%; display: flex; justify-content: center; align-items: center; position: relative;
    }
    .timeline-menu-btn {
      position: absolute; right: -10px; top: 50%; transform: translateY(-50%);
      color: rgba(255,255,255,0.4); cursor: pointer;
      width: 32px; height: 32px;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      transition: all 0.2s;
    }
    .timeline-menu-btn:hover { color: #fff; background: rgba(255,255,255,0.1); }
    .timeline-menu {
      position: absolute; bottom: 100%; right: -20px; margin-bottom: 10px;
      background: rgba(30,30,30,0.95); border: 1px solid rgba(255,255,255,0.1);
      border-radius: 8px; padding: 4px; display: none;
      box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 1001; min-width: 140px;
    }
    .timeline-menu.show { display: block; }
    .timeline-menu-item {
      padding: 8px 12px; font-size: 13px; color: rgba(255,255,255,0.9);
      cursor: pointer; border-radius: 4px; display: flex; align-items: center; gap: 8px;
    }
    .timeline-menu-item:hover { background: rgba(255,255,255,0.1); }
    .timeline-menu-item.danger { color: #ff6b6b; }
    .timeline-menu-item.danger:hover { background: rgba(220, 53, 69, 0.1); }
    
    .delete-controls {
      width: 100%; display: flex; flex-direction: column; align-items: center; gap: 8px;
      margin-top: 4px; animation: slideDown 0.3s ease;
    }
    .btn-delete-confirm {
      background: #dc3545; color: white; border: none; padding: 8px 16px;
      border-radius: 20px; font-size: 13px; font-weight: 500; cursor: pointer;
      display: flex; align-items: center; gap: 6px; transition: all 0.2s;
      box-shadow: 0 4px 12px rgba(220, 53, 69, 0.4);
    }
    .btn-delete-confirm:hover { background: #bd2130; transform: scale(1.05); }
    .btn-delete-cancel {
      background: none; border: none; color: rgba(255,255,255,0.5);
      font-size: 12px; cursor: pointer; margin-top: 4px;
    }
    .btn-delete-cancel:hover { color: #fff; text-decoration: underline; }
    .delete-info { font-size: 11px; color: #ff6b6b; margin-top: 4px; }
    .delete-info { font-size: 11px; color: #ff6b6b; margin-top: 4px; }
    @keyframes slideDown { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes spin { 100% { transform: rotate(360deg); } }
    .spin-anim { animation: spin 1s linear infinite; display: inline-block; }
    .timeline-date {
      font-size: 14px; font-weight: 500; color: rgba(255,255,255,0.85); letter-spacing: 0.3px;
    }
    .timeline-slider {
      width: 100%; height: 4px; -webkit-appearance: none; appearance: none;
      background: rgba(255,255,255,0.2); border-radius: 2px; outline: none;
    }
    .timeline-slider::-webkit-slider-thumb {
      -webkit-appearance: none; width: 16px; height: 16px; border-radius: 50%;
      background: #007bff; cursor: pointer; box-shadow: 0 2px 8px rgba(0,123,255,0.4);
    }
    .timeline-slider::-moz-range-thumb {
      width: 16px; height: 16px; border-radius: 50%; background: #007bff;
      cursor: pointer; border: none; box-shadow: 0 2px 8px rgba(0,123,255,0.4);
    }
    
    /* Text popup */
    .text-popup-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 2000; display: none; }
    .text-popup-overlay.show { display: block; }
    .text-popup {
      position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
      background: rgba(30,30,30,0.98); backdrop-filter: blur(40px); border-radius: 16px;
      border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 20px 60px rgba(0,0,0,0.8);
      max-width: 600px; max-height: 80vh; overflow: hidden; z-index: 2001; display: none;
    }
    .text-popup.show { display: block; }
    .popup-header {
      padding: 20px; border-bottom: 1px solid rgba(255,255,255,0.1);
      display: flex; justify-content: space-between; align-items: center;
    }
    .popup-body { padding: 20px; max-height: 60vh; overflow-y: auto; }
    .popup-body pre {
      white-space: pre-wrap; word-wrap: break-word; margin: 0;
      color: rgba(255,255,255,0.9); font-size: 14px; user-select: text;
    }
    .popup-footer {
      padding: 20px; border-top: 1px solid rgba(255,255,255,0.1);
      display: flex; justify-content: flex-end; gap: 12px;
    }

    /* Settings Modal */
    .settings-modal-overlay {
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.6); backdrop-filter: blur(5px);
      z-index: 2000; opacity: 0; pointer-events: none;
      transition: opacity 0.3s ease; display: flex; align-items: center; justify-content: center;
    }
    .settings-modal-overlay.show { opacity: 1; pointer-events: auto; }
    
    .settings-modal {
      background: #1e1e1e; width: 880px; max-width: 92vw;
      max-height: 90vh; overflow: hidden;
      border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.1);
      box-shadow: 0 20px 60px rgba(0,0,0,0.5); transform: translateY(20px);
      transition: transform 0.3s ease; display: flex; flex-direction: column;
    }
    .settings-modal-overlay.show .settings-modal { transform: translateY(0); }

    .settings-modal-header {
      flex: 0 0 auto;
      padding: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      display: flex; justify-content: space-between; align-items: center;
    }
    .settings-modal-header h2 { font-size: 20px; font-weight: 600; margin: 0; }

    .settings-modal-body {
      flex: 1 1 auto; min-height: 0; overflow-y: auto;
      padding: 20px;
      display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 20px 24px; align-content: start;
    }
    .settings-modal-body .form-group { margin: 0; }
    .settings-modal-body::-webkit-scrollbar { width: 10px; }
    .settings-modal-body::-webkit-scrollbar-thumb {
      background: rgba(255,255,255,0.15); border-radius: 8px;
    }
    .settings-modal-body::-webkit-scrollbar-thumb:hover {
      background: rgba(255,255,255,0.25);
    }

    .settings-modal-footer {
      flex: 0 0 auto;
      padding: 20px; border-top: 1px solid rgba(255, 255, 255, 0.1);
      display: flex; justify-content: flex-end; gap: 10px;
    }
    .btn {
      padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer;
      font-size: 14px; transition: all 0.2s;
    }
    .btn-primary {
      background: #007bff; color: white;
    }
    .btn-primary:hover { background: #0056b3; }
    .btn-secondary {
      background: rgba(255,255,255,0.1); color: white;
    }
    .btn-secondary:hover { background: rgba(255,255,255,0.2); }
    .close-btn {
      background: none; border: none; color: rgba(255,255,255,0.6);
      font-size: 24px; cursor: pointer; padding: 0; line-height: 1;
    }
    .close-btn:hover { color: #fff; }
    
    /* Sidebar toggle button */
    .sidebar-toggle {
      position: fixed; top: 20px; left: 20px; z-index: 1000;
      background: rgba(20,20,20,0.75); backdrop-filter: blur(30px);
      border: 1px solid rgba(255,255,255,0.15); border-radius: 12px;
      width: 44px; height: 44px; display: flex; align-items: center; justify-content: center;
      cursor: pointer; transition: all 0.2s; color: rgba(255,255,255,0.6);
    }
    .sidebar-toggle:hover {
      background: rgba(30,30,30,0.85); border-color: rgba(0,123,255,0.5);
      color: #fff; box-shadow: 0 4px 16px rgba(0,0,0,0.3);
    }
    
    /* Sidebar */
    .sidebar {
      position: fixed; top: 0; left: -400px; width: 400px; height: 100vh;
      background: rgba(20,20,20,0.98); backdrop-filter: blur(40px);
      border-right: 1px solid rgba(255,255,255,0.1); box-shadow: 0 0 60px rgba(0,0,0,0.8);
      z-index: 1100; transition: left 0.3s ease; padding: 80px 24px 24px;
      overflow-y: auto;
    }
    .sidebar.open { left: 0; }
    .sidebar-close {
      position: absolute; top: 20px; right: 20px; background: none; border: none;
      color: rgba(255,255,255,0.6); font-size: 24px; cursor: pointer; padding: 0;
    }
    .sidebar-close:hover { color: #fff; }
    .sidebar-section {
      margin-bottom: 24px; padding: 16px; background: rgba(255,255,255,0.03);
      border-radius: 12px; border: 1px solid rgba(255,255,255,0.05);
    }
    .sidebar-section h3 {
      font-size: 14px; font-weight: 600; margin-bottom: 12px;
      color: rgba(255,255,255,0.7); text-transform: uppercase; letter-spacing: 0.5px;
    }
    .sidebar-section pre {
      white-space: pre-wrap; word-wrap: break-word; margin: 0;
      font-size: 13px; color: rgba(255,255,255,0.8); line-height: 1.5;
    }
    .sidebar-btn {
      width: 100%; padding: 10px 16px; margin-bottom: 8px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.05);
      color: rgba(255,255,255,0.9); cursor: pointer; font-size: 14px;
      transition: all 0.2s; display: flex; align-items: center; gap: 8px;
    }
    .sidebar-btn:hover {
      background: rgba(255,255,255,0.1); border-color: rgba(0,123,255,0.6);
    }
    .sidebar-btn.primary {
      background: rgba(0,123,255,0.8); border-color: rgba(0,123,255,1);
    }
    .sidebar-btn.primary:hover { background: rgba(0,123,255,1); }
    
    /* Toggle switch */
    .toggle-switch {
      position: relative; display: inline-block; width: 48px; height: 26px;
    }
    .toggle-switch input { opacity: 0; width: 0; height: 0; }
    .toggle-slider {
      position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
      background-color: rgba(255,255,255,0.2); transition: 0.3s; border-radius: 26px;
    }
    .toggle-slider:before {
      position: absolute; content: ""; height: 18px; width: 18px; left: 4px; bottom: 4px;
      background-color: white; transition: 0.3s; border-radius: 50%;
    }
    input:checked + .toggle-slider { background-color: #007bff; }
    input:checked + .toggle-slider:before { transform: translateX(22px); }
    
    .ocr-mode-selector {
      display: flex; align-items: center; justify-content: space-between;
      padding: 12px 16px; background: rgba(255,255,255,0.05);
      border-radius: 8px; margin-bottom: 16px;
    }
    .ocr-mode-label {
      font-size: 13px; color: rgba(255,255,255,0.8);
    }
    
    /* AI Config Modal */
    .config-modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 3000;
      display: none; align-items: center; justify-content: center;
    }
    .config-modal-overlay.show { display: flex; }
    .config-modal {
      background: rgba(30,30,30,0.98); backdrop-filter: blur(40px);
      border-radius: 16px; border: 1px solid rgba(255,255,255,0.1);
      box-shadow: 0 20px 60px rgba(0,0,0,0.8); width: 500px; max-width: 90vw;
    }
    .config-modal-header {
      padding: 24px; border-bottom: 1px solid rgba(255,255,255,0.1);
      display: flex; justify-content: space-between; align-items: center;
    }
    .config-modal-header h2 {
      font-size: 18px; font-weight: 600; margin: 0; color: #fff;
    }
    .config-modal-body { padding: 24px; }
    .form-group {
      margin-bottom: 20px;
    }
    .form-group label {
      display: block; margin-bottom: 8px; font-size: 13px;
      color: rgba(255,255,255,0.7); font-weight: 500;
    }
    .form-group select, .form-group input:not([type="checkbox"]):not([type="radio"]) {
      width: 100%; padding: 12px 16px; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.05);
      color: #fff; font-size: 14px; transition: all 0.2s;
    }
    .form-group select:focus, .form-group input:focus {
      outline: none; border-color: rgba(0,123,255,0.6);
      background: rgba(255,255,255,0.08);
    }
    .form-group small {
      display: block; margin-top: 6px; font-size: 12px;
      color: rgba(255,255,255,0.5);
    }

    /* ===== Redesigned menu: action cluster, text dialog, command palette ===== */
    @keyframes orl-pop { from { opacity: 0; transform: translate(-50%, -48%) scale(.97); } to { opacity: 1; transform: translate(-50%, -50%) scale(1); } }
    @keyframes orl-palette-in { from { opacity: 0; transform: translateX(-50%) translateY(-10px) scale(.97); } to { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); } }
    @keyframes orl-scrim-in { from { opacity: 0; } to { opacity: 1; } }

    /* Action cluster (top-right) */
    .action-cluster {
      position: fixed; top: 24px; right: 24px; z-index: 1000;
      display: flex; align-items: center; gap: 8px;
    }
    .action-btn {
      display: flex; align-items: center; gap: 7px; height: 40px; padding: 0 14px;
      border-radius: 20px; border: 1px solid rgba(255,255,255,0.16);
      background: rgba(20,20,20,0.78); backdrop-filter: blur(30px);
      color: #fff; font-size: 13px; font-weight: 500; cursor: pointer;
      transition: all 0.15s; font-family: inherit;
    }
    .action-btn i { font-size: 15px; }
    .action-btn:hover { background: rgba(40,40,40,0.92); border-color: rgba(255,255,255,0.28); }
    .action-btn.ai {
      padding: 0 16px; border: none; font-weight: 600;
      background: linear-gradient(180deg, #2b86ff, #0d6efd);
      box-shadow: 0 4px 16px rgba(13,110,253,0.45);
    }
    .action-btn.ai:hover { filter: brightness(1.08); }

    /* Text dialog */
    .text-dialog-scrim {
      position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 2000;
      display: none; animation: orl-scrim-in 0.18s ease;
    }
    .text-dialog-scrim.show { display: block; }
    .text-dialog {
      position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
      width: 640px; max-width: 82vw; max-height: 78vh; z-index: 2001;
      background: rgba(28,28,30,0.98); backdrop-filter: blur(40px);
      border: 1px solid rgba(255,255,255,0.12); border-radius: 18px;
      box-shadow: 0 30px 90px rgba(0,0,0,0.7);
      display: none; flex-direction: column; overflow: hidden;
    }
    .text-dialog.show { display: flex; animation: orl-pop 0.2s cubic-bezier(.2,.8,.2,1); }
    .td-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 18px 20px 14px; flex: 0 0 auto;
    }
    .td-title { display: flex; align-items: center; gap: 11px; }
    .td-title > span:first-child { font-size: 16px; font-weight: 600; color: #fff; }
    .td-ts {
      font-size: 11px; color: rgba(255,255,255,0.5);
      background: rgba(255,255,255,0.07); padding: 3px 9px; border-radius: 20px;
    }
    .td-close {
      width: 30px; height: 30px; border-radius: 50%; border: none;
      background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.7);
      font-size: 18px; line-height: 1; cursor: pointer; font-family: inherit;
      display: flex; align-items: center; justify-content: center; transition: all 0.15s;
    }
    .td-close:hover { background: rgba(255,255,255,0.16); color: #fff; }
    .td-toolbar {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      padding: 0 20px 14px; flex: 0 0 auto;
    }
    .mode-switch {
      display: flex; align-items: center; gap: 8px;
      background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
      border-radius: 10px; padding: 6px 11px;
    }
    .mode-label { font-size: 11px; font-weight: 500; color: rgba(255,255,255,0.45); transition: color 0.2s; }
    .mode-label.active { color: #fff; }
    .mode-label.ai.active { color: #7fc0ff; }
    .src-track {
      width: 34px; height: 18px; border: none; padding: 0; border-radius: 10px;
      background: rgba(255,255,255,0.22); position: relative; cursor: pointer; transition: background 0.2s;
    }
    .mode-switch.on .src-track { background: #0d6efd; }
    .src-knob {
      position: absolute; top: 2px; left: 2px; width: 14px; height: 14px;
      border-radius: 50%; background: #fff; transition: left 0.2s; pointer-events: none;
    }
    .mode-switch.on .src-knob { left: 18px; }
    .dialog-btn {
      display: flex; align-items: center; gap: 7px; height: 34px; padding: 0 14px;
      border-radius: 10px; border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.05); color: #fff; font-size: 12.5px;
      cursor: pointer; font-family: inherit; transition: all 0.15s;
    }
    .dialog-btn:hover { background: rgba(255,255,255,0.1); }
    .dialog-btn.icon { width: 34px; padding: 0; justify-content: center; margin-left: auto; font-size: 14px; color: rgba(255,255,255,0.75); }
    .dialog-btn.icon.active { background: rgba(13,110,253,0.25); border-color: rgba(13,110,253,0.45); color: #fff; }
    .dialog-btn.ai {
      border: none; font-weight: 600;
      background: linear-gradient(180deg, #2b86ff, #0d6efd);
      box-shadow: 0 3px 12px rgba(13,110,253,0.4);
    }
    .dialog-btn.ai:hover { filter: brightness(1.08); }
    .dialog-btn.disabled { opacity: 0.4; pointer-events: none; }
    .dialog-btn.ai.running { opacity: 0.55; pointer-events: none; }
    .ai-spinner {
      display: none; width: 12px; height: 12px; border: 2px solid rgba(255,255,255,0.35);
      border-top-color: #fff; border-radius: 50%; animation: orl-spin 0.7s linear infinite;
    }
    @keyframes orl-spin { to { transform: rotate(360deg); } }
    .td-banner {
      display: none; align-items: center; gap: 8px; margin: 0 20px 12px;
      font-size: 11.5px; color: #7fc0ff;
      background: rgba(13,110,253,0.12); border: 1px solid rgba(13,110,253,0.3);
      border-radius: 9px; padding: 8px 12px;
    }
    .td-banner.show { display: flex; }
    /* Find-in-text bar */
    .td-find {
      display: none; align-items: center; gap: 8px; margin: 0 20px 12px;
      padding: 7px 8px 7px 12px; border-radius: 10px;
      background: rgba(255,255,255,0.05); border: 1px solid rgba(13,110,253,0.45);
    }
    .td-find.show { display: flex; animation: orl-td-fade 0.15s ease; }
    @keyframes orl-td-fade { from { opacity: 0; transform: translateY(-5px); } to { opacity: 1; transform: translateY(0); } }
    .td-find-icon { color: rgba(255,255,255,0.45); font-size: 13px; flex: none; }
    .td-find input {
      flex: 1; min-width: 60px; background: transparent; border: none; outline: none;
      color: #fff; font-size: 13px; font-family: inherit;
    }
    .td-find-count { font-size: 11.5px; color: rgba(255,255,255,0.5); font-variant-numeric: tabular-nums; flex: none; }
    .td-find-btn {
      width: 26px; height: 26px; border-radius: 7px; border: none; cursor: pointer; flex: none;
      background: transparent; color: rgba(255,255,255,0.6); font-size: 12px;
      display: flex; align-items: center; justify-content: center; transition: all 0.12s;
    }
    .td-find-btn:hover { background: rgba(255,255,255,0.1); color: #fff; }
    /* Text body (scroll container) */
    .td-body {
      flex: 1 1 auto; overflow-y: auto; padding: 2px 20px 18px; min-height: 140px; position: relative;
      white-space: pre-wrap; word-break: break-word; user-select: text;
      font-size: 13.5px; line-height: 1.7; color: rgba(255,255,255,0.88); font-family: inherit;
    }
    .td-body.ai-size { font-size: 14.5px; }
    .td-state {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      min-height: 180px; gap: 8px; color: rgba(255,255,255,0.4); white-space: normal; text-align: center;
    }
    .td-state .td-state-hint { font-size: 12px; color: rgba(255,255,255,0.35); }
    .td-body mark.find-hl { background: rgba(13,110,253,0.32); color: #eaf4ff; border-radius: 3px; padding: 0 1px; }
    .td-body mark.find-hl.current { background: #0d6efd; color: #fff; }

    /* Command palette actions (under the search bar) */
    .palette-actions {
      position: fixed; top: 74px; left: 50%; transform: translateX(-50%);
      width: 480px; max-width: 74vw; z-index: 1400;
      background: rgba(28,28,30,0.98); backdrop-filter: blur(40px);
      border: 1px solid rgba(255,255,255,0.12); border-radius: 16px;
      box-shadow: 0 24px 70px rgba(0,0,0,0.7); padding: 8px;
      display: none; transform-origin: top center;
    }
    .palette-actions.show { display: block; animation: orl-palette-in 0.22s cubic-bezier(.2,.8,.2,1); }
    .palette-section-label {
      font-size: 10px; letter-spacing: 1.3px; text-transform: uppercase;
      color: rgba(255,255,255,0.38); padding: 8px 10px 6px;
    }
    .palette-action {
      display: flex; gap: 11px; align-items: center; padding: 10px;
      border-radius: 10px; cursor: pointer; font-size: 13.5px; color: #fff;
    }
    .palette-action i { width: 20px; text-align: center; font-size: 15px; color: rgba(255,255,255,0.8); }
    .palette-action:hover { background: rgba(255,255,255,0.09); }
    .palette-action.muted { color: rgba(255,255,255,0.62); }
    .palette-action.danger:hover { background: rgba(255,90,80,0.16); }
    .palette-action.danger:hover i, .palette-action.danger:hover span { color: #ff8a80; }
    .config-modal-footer {
      padding: 20px 24px; border-top: 1px solid rgba(255,255,255,0.1);
      display: flex; justify-content: flex-end; gap: 12px;
    }
    
    /* Toast notifications */
    .toast-container {
      position: fixed; top: 80px; right: 20px; z-index: 99999 !important;
      display: flex; flex-direction: column; gap: 12px; pointer-events: none;
    }
    .toast {
      background: rgba(30,30,30,0.98); backdrop-filter: blur(40px);
      border-radius: 12px; border: 1px solid rgba(255,255,255,0.1);
      box-shadow: 0 8px 32px rgba(0,0,0,0.6); padding: 16px 20px;
      display: flex; align-items: center; gap: 12px; min-width: 300px;
      animation: slideIn 0.3s ease-out; pointer-events: auto;
    }
    .toast.success { border-left: 3px solid #28a745; }
    .toast.error { border-left: 3px solid #dc3545; }
    .toast.info { border-left: 3px solid #007bff; }
    .toast-icon {
      font-size: 20px; flex-shrink: 0;
    }
    .toast.success .toast-icon { color: #28a745; }
    .toast.error .toast-icon { color: #dc3545; }
    .toast.info .toast-icon { color: #007bff; }
    .toast-content {
      flex: 1; font-size: 14px; color: rgba(255,255,255,0.9);
    }
    .toast-close {
      background: none; border: none; color: rgba(255,255,255,0.5);
      cursor: pointer; font-size: 18px; padding: 0; line-height: 1;
    }
    .toast-close:hover { color: #fff; }
    /* Calendar */
    .calendar-btn {
      margin-left: 10px; color: rgba(255,255,255,0.4); cursor: pointer;
      transition: all 0.2s; font-size: 16px; display: flex; align-items: center;
    }
    .calendar-btn:hover { color: #fff; transform: scale(1.1); }
    
    .calendar-wrapper {
      position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
      margin-bottom: 20px; background: rgba(30,30,30,0.98); 
      backdrop-filter: blur(40px); border-radius: 16px;
      border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 20px 60px rgba(0,0,0,0.6);
      padding: 20px; z-index: 1002; display: none; width: 320px;
    }
    .calendar-wrapper.show { display: block; animation: slideUp 0.3s ease; }
    
    .calendar-header {
      display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;
    }
    .calendar-title { font-weight: 600; font-size: 16px; color: #fff; }
    .calendar-nav-btn {
      background: rgba(255,255,255,0.1); border: none; color: #fff;
      width: 28px; height: 28px; border-radius: 50%; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      transition: all 0.2s;
    }
    .calendar-nav-btn:hover { background: rgba(255,255,255,0.2); }
    
    .calendar-grid {
      display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; text-align: center;
    }
    .calendar-day-header {
      font-size: 12px; color: rgba(255,255,255,0.4); margin-bottom: 8px; font-weight: 500;
    }
    .calendar-day {
      width: 32px; height: 32px; border-radius: 50%; font-size: 13px;
      display: flex; align-items: center; justify-content: center;
      color: rgba(255,255,255,0.3); position: relative;
    }
    .calendar-day.active {
      color: #fff; cursor: pointer; background: rgba(255,255,255,0.05);
    }
    .calendar-day.active:hover { background: rgba(0,123,255,0.3); }
    .calendar-day.has-recording::after {
      content: ''; position: absolute; bottom: 4px; left: 50%; transform: translateX(-50%);
      width: 4px; height: 4px; background: #007bff; border-radius: 50%;
    }
    .calendar-day.selected {
      background: #007bff; color: white;
    }
    .calendar-day.selected::after { background: white; }
    
    /* Custom CSS Tooltip */
    .tooltip-container {
      position: relative;
      display: inline-block;
    }
    .tooltip-text {
      visibility: hidden;
      width: 280px;
      background: rgba(40, 40, 40, 0.98);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #fff;
      text-align: center;
      border-radius: 8px;
      padding: 10px 14px;
      position: absolute;
      z-index: 2200;
      bottom: 135%;
      left: 50%;
      transform: translateX(-50%) scale(0.95);
      opacity: 0;
      transition: opacity 0.2s, transform 0.2s;
      font-size: 13px;
      line-height: 1.4;
      pointer-events: none;
      box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    }
    .tooltip-text::after {
      content: "";
      position: absolute;
      top: 100%;
      left: 50%;
      margin-left: -6px;
      border-width: 6px;
      border-style: solid;
      border-color: rgba(40, 40, 40, 0.98) transparent transparent transparent;
    }
    .tooltip-container:hover .tooltip-text {
      visibility: visible;
      opacity: 1;
      transform: translateX(-50%) scale(1);
    }
    
    @keyframes slideUp { from { opacity: 0; transform: translate(-50%, 10px); } to { opacity: 1; transform: translate(-50%, 0); } }
    
    /* Responsive adjustments */
    @media (max-width: 768px) {
      .sidebar {
        width: 90vw;
        left: -90vw;
      }
      .sidebar.open { left: 0; }
      
      .search-input {
        width: calc(100vw - 80px);
        font-size: 14px;
        padding: 10px 40px 10px 16px;
      }
      
      .search-results {
        top: 70px;
        left: 10px;
        right: 10px;
        width: auto;
        padding: 12px;
      }
      
      .results-grid {
        grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
        gap: 12px;
      }
      
      .timeline-pill {
        min-width: calc(100vw - 40px);
        padding: 12px 20px;
      }
      
      .timeline-date {
        font-size: 12px;
      }
      
      .screenshot-area {
        padding: max(60px, 8vh) 10px max(100px, 12vh);
      }
      
      .sidebar-toggle {
        top: 15px;
        left: 15px;
        width: 40px;
        height: 40px;
      }
      
      .config-modal {
        width: 90vw;
      }
    }
    
    @media (max-width: 480px) {
      .search-container {
        top: 10px;
        right: 10px;
        left: 60px;
      }
      
      .search-input {
        width: 100%;
      }
      
      .timeline-pill {
        padding: 10px 16px;
      }
      
      .timeline-date {
        font-size: 11px;
      }
      
      .text-icon {
        width: 28px;
        height: 28px;
        font-size: 14px;
      }
    }
    .jump-to-latest-btn {
      position: absolute;
      bottom: 80px;
      right: 20px;
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: rgba(0, 123, 255, 0.9);
      color: white;
      border: none;
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
      display: none; /* Hidden by default */
      align-items: center;
      justify-content: center;
      cursor: pointer;
      z-index: 1000;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      transform: translateY(0);
    }
    .jump-to-latest-btn:hover {
      background: #007bff;
      transform: translateY(-2px);
      box-shadow: 0 6px 16px rgba(0,0,0,0.4);
    }
    .jump-to-latest-btn.show {
      display: flex;
      animation: popIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    @keyframes popIn {
      from { opacity: 0; transform: scale(0.5) translateY(10px); }
      to { opacity: 1; transform: scale(1) translateY(0); }
    }
  </style>
</head>
<body>
  <div class="fullscreen-container">
    <!-- Action cluster (top-right) -->
    <div class="action-cluster">
      <button class="action-btn" onclick="openTextDialog(false)" title="Show this screen's text">
        <i class="bi bi-body-text"></i> Text
      </button>
    </div>

    <!-- Text dialog (current frame OCR text + actions) -->
    <div class="text-dialog-scrim" id="textDialogScrim" onclick="closeTextDialog()"></div>
    <div class="text-dialog" id="textDialog" role="dialog" aria-modal="true" aria-hidden="true">
      <div class="td-header">
        <div class="td-title">
          <span>Screen text</span>
          <span class="td-ts" id="textDialogTs"></span>
        </div>
        <button class="td-close" onclick="closeTextDialog()" aria-label="Close" title="Close (Esc)">&times;</button>
      </div>
      <div class="td-toolbar">
        <div class="mode-switch" id="sourceSwitch">
          <span class="mode-label" id="modeLabelBase">Base</span>
          <button class="src-track" onclick="toggleOCRMode()" title="Switch text source" aria-label="Switch text source"><span class="src-knob"></span></button>
          <span class="mode-label ai" id="modeLabelAi">AI</span>
        </div>
        <button class="dialog-btn ai" id="btnRunAI" onclick="runAIOCR()">
          <span class="ai-spinner"></span><i class="bi bi-stars ai-star"></i> <span class="run-label">Extract with AI</span>
        </button>
        <button class="dialog-btn" id="btnCopyText" onclick="copyExtractedText()">
          <i class="bi bi-clipboard"></i> <span class="copy-label">Copy all</span>
        </button>
        <button class="dialog-btn icon" id="btnFind" onclick="openDialogFind()" title="Find in text (⌘F)"><i class="bi bi-search"></i></button>
      </div>
      <div class="td-find" id="dialogFind">
        <i class="bi bi-search td-find-icon"></i>
        <input type="text" id="dialogFindInput" placeholder="Find in text" autocomplete="off"
               oninput="dialogFindRun()" onkeydown="dialogFindKey(event)">
        <span class="td-find-count" id="dialogFindCount"></span>
        <button class="td-find-btn" onclick="dialogFindStep(-1)" title="Previous (⇧↵)"><i class="bi bi-chevron-up"></i></button>
        <button class="td-find-btn" onclick="dialogFindStep(1)" title="Next (↵)"><i class="bi bi-chevron-down"></i></button>
        <button class="td-find-btn" onclick="closeDialogFind()" title="Close (Esc)"><i class="bi bi-x-lg"></i></button>
      </div>
      <div class="td-banner" id="textDialogBanner">
        <i class="bi bi-stars"></i> AI transcription · structure and accents restored
      </div>
      <div id="extractedText" class="td-body"></div>
    </div>

    <!-- Command palette: global actions (shown under the search bar on focus / Cmd-K) -->
    <div class="palette-actions" id="paletteActions">
      <div class="palette-section-label">Actions</div>
      <div class="palette-action" onmousedown="event.preventDefault()" onclick="paletteAction('settings')">
        <i class="bi bi-gear"></i><span>Settings</span>
      </div>
      <div class="palette-action muted" onmousedown="event.preventDefault()" onclick="paletteAction('hide')">
        <i class="bi bi-window-dash"></i><span>Hide window</span>
      </div>
      <div class="palette-action muted danger" onmousedown="event.preventDefault()" onclick="paletteAction('quit')">
        <i class="bi bi-power"></i><span>Quit OpenReLife</span>
      </div>
    </div>

    <!-- Search bar -->
    <div class="search-container">
      <div class="search-wrapper" id="searchWrapper">
        <input type="text" class="search-input" id="searchInput" placeholder="Search your history..."
               role="combobox" aria-expanded="false" aria-controls="resultsGrid"
               aria-autocomplete="list" autocomplete="off">
        <i class="bi bi-search search-icon" id="searchIcon"></i>
        <i class="bi bi-x-circle-fill clear-icon" id="searchClear" style="display: none;"></i>
      </div>
    </div>

    <!-- Search results -->
    <div class="search-results" id="searchResults"></div>
    <div id="searchLive" class="visually-hidden" aria-live="polite"></div>
    
    <!-- Screenshot area -->
    <div class="screenshot-area" id="screenshotArea">
      <div class="screenshot-wrapper">
        <img id="screenshot" src="/static/{{timestamps[0]}}.webp" alt="Screenshot">
        <div class="text-overlay" id="textOverlay"></div>
      </div>
    </div>

    <!-- Jump to latest button -->
    <button class="jump-to-latest-btn" id="jumpToLatestBtn" onclick="jumpToLatest()" title="Jump to latest">
      <i class="bi bi-arrow-right-short" style="font-size: 24px;"></i>
    </button>
    
    <!-- Timeline -->
    <div class="timeline">
      <div class="timeline-pill" id="timelinePill">
        <div class="timeline-header">
          <div style="display: flex; align-items: center; justify-content: center; gap: 8px; white-space: nowrap;">
            <div class="timeline-date" id="timelineDate">{{timestamps[0] | timestamp_to_human_readable}}</div>
            <div class="calendar-btn" onclick="toggleCalendar(event)" style="margin: 0;">
              <i class="bi bi-calendar-event"></i>
            </div>
          </div>
          
          <!-- Calendar Popup -->
          <div class="calendar-wrapper" id="calendarWrapper" onclick="event.stopPropagation()">
            <div class="calendar-header">
              <button class="calendar-nav-btn" onclick="prevMonth()">
                <i class="bi bi-chevron-left"></i>
              </button>
              <div class="calendar-title" id="calendarTitle">December 2025</div>
              <button class="calendar-nav-btn" onclick="nextMonth()">
                <i class="bi bi-chevron-right"></i>
              </button>
            </div>
            <div class="calendar-grid" id="calendarGrid">
              <!-- Days will be generated here -->
            </div>
          </div>

          <div class="timeline-menu-btn" onclick="toggleTimelineMenu(event)">
            <i class="bi bi-three-dots-vertical"></i>
          </div>
          <div class="timeline-menu" id="timelineMenu">
             <div class="timeline-menu-item" onclick="openSettings()">
               <i class="bi bi-gear"></i> Settings
             </div>
             <div class="timeline-menu-item danger" onclick="enterDeleteMode()">
               <i class="bi bi-trash"></i> Delete
             </div>
          </div>
        </div>
        
        <input type="range" class="timeline-slider" id="timelineSlider" 
               min="0" max="{{timestamps|length - 1}}" value="{{timestamps|length - 1}}">
               
        <div class="delete-controls" id="deleteControls" style="display: none;">
           <button class="btn-delete-confirm" id="btnConfirmDelete" onclick="confirmDelete()">
             <i class="bi bi-trash-fill"></i> Delete selection
           </button>
           <div class="delete-info" id="deleteInfo">1 screenshot</div>
           <button class="btn-delete-cancel" onclick="exitDeleteMode()">Cancel</button>
        </div>
      </div>
    </div>
  </div>
  
  <!-- Settings Modal -->
  <div class="settings-modal-overlay" id="settingsModalOverlay" onclick="if(event.target===this) closeSettings()">
    <div class="settings-modal">
      <div class="settings-modal-header">
        <h2>Settings</h2>
        <button class="close-btn" onclick="closeSettings()">&times;</button>
      </div>
      <div class="settings-modal-body">
        <div class="form-group">
          <label>Screenshot Retention</label>
          <select id="retentionSelect" class="form-control" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
            <option value="-1">Keep Forever (Default)</option>
            <option value="7">7 Days</option>
            <option value="30">30 Days</option>
            <option value="90">90 Days</option>
            <option value="365">1 Year</option>
          </select>
          <small class="form-text text-muted" style="margin-top: 8px;">
            Screenshots older than this period will be automatically deleted daily.
            <br><span style="color: #ffc107;"><i class="bi bi-exclamation-triangle"></i> Changing this will permanently delete old data.</span>
          </small>
        </div>
        
        <div class="form-group">
          <label>
            Screenshot Interval (seconds)
            <div class="tooltip-container">
              <i class="bi bi-question-circle" style="cursor: help; margin-left: 4px; color: rgba(255,255,255,0.4);"></i>
              <span class="tooltip-text">Time between screen captures. OCR runs separately in the background and does not affect capture speed.</span>
            </div>
          </label>
          <input type="text" inputmode="numeric" id="intervalInput" class="form-control" oninput="this.value = this.value.replace(/[^0-9]/g, ''); checkIntervalWarning(this.value)" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
          <div id="intervalWarning" style="margin-top: 12px; color: #ffc107; display: none; background: rgba(255, 193, 7, 0.1); padding: 12px; border-radius: 8px; border-left: 4px solid #ffc107; font-size: 13px;">
            <i class="bi bi-exclamation-triangle-fill" style="margin-right: 8px;"></i>
            <strong>Warning:</strong> setting a value below 3 seconds will cause an <strong>unproportional</strong> increase in CPU and disk usage. Proceed only if strictly necessary.
          </div>
        </div>

        <div class="form-group">
          <label>Screenshot Quality</label>
          <select id="qualitySelect" class="form-control" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
            <option value="low">Low (80% scale, 80% quality)</option>
            <option value="medium">Medium (95% scale, 95% quality)</option>
            <option value="high">High (Original scale, Lossless)</option>
          </select>
          <small class="form-text text-muted" style="margin-top: 8px;">
            Higher quality will result in larger file sizes.
          </small>
        </div>

        <div class="form-group">
          <label>
            OCR Processing Interval (seconds)
            <div class="tooltip-container">
              <i class="bi bi-question-circle" style="cursor: help; margin-left: 4px; color: rgba(255,255,255,0.4);"></i>
              <span class="tooltip-text">How long to wait between OCR batches. Higher values reduce CPU usage but delay text extraction. Screenshots are always captured regardless of this setting.</span>
            </div>
          </label>
          <input type="text" inputmode="numeric" id="ocrCooldownInput" class="form-control" oninput="this.value = this.value.replace(/[^0-9]/g, '')" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
          <small class="form-text text-muted" style="margin-top: 8px;">
            Default: 90s. Lower = faster text search, higher = less CPU usage.
          </small>
        </div>

        <div id="ocrEngineSection" class="form-group" style="display: none;">
          <label style="display:block; font-weight:600; margin-bottom:6px;">OCR Engine</label>
          <label style="display:flex; align-items:center; gap:8px;">
            <input type="checkbox" id="useAppleVisionCheckbox">
            <span>Use Apple Vision (recommended, ~30× faster)</span>
          </label>
          <p style="margin-top:6px; color:#666; font-size:12px;">
            Native macOS text recognition. Falls back to doctr automatically if a frame fails.
            Available only on Mac with Apple Silicon.
          </p>
        </div>

        <div class="form-group">
          <label>
            OCR Compute Mode
            <div class="tooltip-container">
              <i class="bi bi-question-circle" style="cursor: help; margin-left: 4px; color: rgba(255,255,255,0.4);"></i>
              <span class="tooltip-text">Controls how aggressively OCR processes screenshots. Affects CPU usage and how quickly text becomes searchable.</span>
            </div>
          </label>
          <select id="ocrComputeModeSelect" class="form-control" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
            <option value="aggressive">Aggressive — large batches, short cooldown, always runs</option>
            <option value="smart" selected>Smart — adapts to battery/charging/idle state</option>
            <option value="on_charge_only">On Charge Only — no OCR on battery, recovers when plugged in</option>
            <option value="eco">Eco — power-saving: single thread, tiny batches, only when fully charged or idle</option>
            <option value="disabled">Disabled — never run OCR (screenshots still captured, text not searchable)</option>
          </select>
          <small class="form-text text-muted" style="margin-top: 8px;">
            <strong>Aggressive:</strong> fastest text availability, highest CPU usage.<br>
            <strong>Smart:</strong> small batches on battery, medium on charge, full recovery when idle+charging.<br>
            <strong>On Charge Only:</strong> zero CPU impact on battery, processes backlog when plugged in.<br>
            <strong>Eco:</strong> minimum power draw — skips on battery, runs slowly when on AC and idle or fully charged.<br>
            <strong>Disabled:</strong> OCR is paused entirely; screenshots are saved but their text is not extracted until you switch to another mode.
          </small>
        </div>

        <div class="form-group">
          <label style="display: flex; align-items: center; cursor: pointer;">
            <input type="checkbox" id="skipIncognitoCheckbox" checked style="width: 18px; height: 18px; margin-right: 10px; accent-color: #0d6efd;">
            Skip recording in incognito/private mode
          </label>
          <small class="form-text text-muted" style="margin-top: 8px;">
            When enabled, screenshots will not be captured while a browser is in incognito or private browsing mode.
          </small>
        </div>

        <div class="form-group">
          <label>
            Server Port
             <div class="tooltip-container">
              <i class="bi bi-question-circle" style="cursor: help; margin-left: 4px; color: rgba(255,255,255,0.4);"></i>
              <span class="tooltip-text">Requires restart to take effect. Default: 8082</span>
            </div>
          </label>
          <input type="text" inputmode="numeric" id="portInput" class="form-control" oninput="this.value = this.value.replace(/[^0-9]/g, '')" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
        </div>
      </div>
      <div class="settings-modal-footer">
        <button class="btn btn-secondary" onclick="closeSettings()">Close</button>
        <button class="btn btn-primary" onclick="saveSettings()">Save Changes</button>
      </div>
    </div>
  </div>
  
  <!-- AI Config Modal -->
  <div class="config-modal-overlay" id="configModalOverlay" onclick="if(event.target===this) closeAIConfig()">
    <div class="config-modal">
      <div class="config-modal-header">
        <h2>AI Provider Settings</h2>
        <button class="close-btn" onclick="closeAIConfig()">&times;</button>
      </div>
      <div class="config-modal-body">
        <div class="form-group">
          <label>AI Provider</label>
          <select id="aiProvider">
            <option value="gemini">Google Gemini</option>
            <option value="openai">OpenAI (GPT-4o)</option>
            <option value="claude">Anthropic Claude</option>
          </select>
          <small>Choose your preferred AI provider for enhanced OCR</small>
        </div>
        <div class="form-group">
          <label>API Key</label>
          <input type="password" id="aiApiKey" placeholder="Enter your API key">
          <small>Your API key is stored locally and never sent to our servers</small>
        </div>
      </div>
      <div class="config-modal-footer">
        <button class="btn btn-secondary" onclick="closeAIConfig()">Cancel</button>
        <button class="btn btn-primary" onclick="saveAIConfig()">
          <i class="bi bi-check-lg"></i> Save Settings
        </button>
      </div>
    </div>
  </div>
  
  <!-- Toast container -->
  <div class="toast-container" id="toastContainer"></div>
  
  <!-- Text popup -->
  <div class="text-popup-overlay" id="textPopupOverlay" onclick="closeTextPopup()"></div>
  <div class="text-popup" id="textPopup">
    <div class="popup-header">
      <strong>Text Block</strong>
      <button class="close-btn" onclick="closeTextPopup()">&times;</button>
    </div>
    <div class="popup-body">
      <pre id="popupText"></pre>
    </div>
    <div class="popup-footer">
      <button class="btn btn-secondary" onclick="closeTextPopup()">Close</button>
      <button class="btn btn-primary" onclick="copyPopupText()">
        <i class="bi bi-clipboard"></i> Copy
      </button>
    </div>
  </div>



  <script>
    let timestamps = {{timestamps|tojson}};
    let entriesData = {{entries_dict|tojson}};
    const slider = document.getElementById('timelineSlider');
    const dateEl = document.getElementById('timelineDate');
    const screenshot = document.getElementById('screenshot');
    const textOverlay = document.getElementById('textOverlay');
    const screenshotArea = document.getElementById('screenshotArea');
    const searchInput = document.getElementById('searchInput');
    const searchResults = document.getElementById('searchResults');
    
    let currentEntry = null;
    let searchTimeout = null;
    let searchController = null;
    
    // Jump to latest logic
    const jumpBtn = document.getElementById('jumpToLatestBtn');
    
    function updateJumpButtonVisibility() {
      const sliderVal = parseInt(slider.value);
      const isAtLatest = sliderVal === parseInt(slider.max);
      
      if (!isAtLatest) {
        jumpBtn.classList.add('show');
      } else {
        jumpBtn.classList.remove('show');
      }
    }
    
    function jumpToLatest() {
      slider.value = slider.max;
      // Manually trigger input event to update display
      slider.dispatchEvent(new Event('input'));
    }
    
    // Deletion mode
    let isDeleteMode = false;
    let deleteStartIndex = -1;
    let deleteEndIndex = -1;
    
    function toggleTimelineMenu(e) {
      e.stopPropagation();
      const menu = document.getElementById('timelineMenu');
      menu.classList.toggle('show');
    }
    
    document.addEventListener('click', () => {
      document.getElementById('timelineMenu').classList.remove('show');
    });
    
    function hideAppWindow() {
      const timelineMenu = document.getElementById('timelineMenu');
      if (timelineMenu) timelineMenu.classList.remove('show');

      const sidebar = document.getElementById('sidebar');
      if (sidebar && sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
      }

      if (window.electronAPI && window.electronAPI.hideWindow) {
        window.electronAPI.hideWindow();
      }
    }

    function quitAppFromMenu() {
      const timelineMenu = document.getElementById('timelineMenu');
      if (timelineMenu) timelineMenu.classList.remove('show');

      const sidebar = document.getElementById('sidebar');
      if (sidebar && sidebar.classList.contains('open')) {
        sidebar.classList.remove('open');
      }

      if (!confirm('Quit OpenReLife?')) return;
      if (window.electronAPI && window.electronAPI.quitApp) {
        window.electronAPI.quitApp();
      }
    }

    function enterDeleteMode() {
      isDeleteMode = true;
      document.getElementById('timelinePill').classList.add('delete-mode');
      document.getElementById('deleteControls').style.display = 'flex';
      document.getElementById('timelineMenu').classList.remove('show');
      
      const currentIdx = timestamps.length - 1 - parseInt(slider.value);
      deleteStartIndex = currentIdx;
      deleteEndIndex = currentIdx;
      updateDeleteInfo();
    }
    
    function exitDeleteMode() {
      isDeleteMode = false;
      document.getElementById('timelinePill').classList.remove('delete-mode');
      document.getElementById('deleteControls').style.display = 'none';
      deleteStartIndex = -1;
      deleteEndIndex = -1;
    }
    
    function updateDeleteInfo() {
      if (!isDeleteMode) return;
      const count = Math.abs(deleteEndIndex - deleteStartIndex) + 1;
      document.getElementById('deleteInfo').textContent = `${count} screenshot${count > 1 ? 's' : ''} to delete`;
    }
    
    async function confirmDelete() {
      if (!confirm('Are you sure you want to delete these screenshots?')) return;
      
      const btn = document.getElementById('btnConfirmDelete');
      const originalText = btn.innerHTML;
      btn.disabled = true;
      btn.style.opacity = '0.7';
      btn.style.cursor = 'not-allowed';
      btn.innerHTML = '<i class="bi bi-arrow-repeat spin-anim"></i> Deleting...';
      
      const start = Math.min(deleteStartIndex, deleteEndIndex);
      const end = Math.max(deleteStartIndex, deleteEndIndex);
      const toDelete = timestamps.slice(start, end + 1);
      
      try {
        const res = await fetch('/api/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({timestamps: toDelete})
        });
        const data = await res.json();
        
        // Remove from local arrays
        timestamps = timestamps.filter(t => !toDelete.includes(t));
        // Reload page to refresh state cleanly
        window.location.reload();
      } catch (e) {
        alert('Error deleting: ' + e);
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.cursor = 'pointer';
        btn.innerHTML = originalText;
      }
    }

    // Lightweight sync: fetches only new timestamps, entry data loaded on-demand
    let syncInterval = null;
    let syncCursor = Date.now() * 1000; // microseconds — start from now
    let syncIndicator = null;

    async function syncData() {
      try {
        const wasAtLatest = timestamps.length > 0 && parseInt(slider.value) === parseInt(slider.max);

        const response = await fetch(`/api/sync?since=${syncCursor}`);
        const data = await response.json();

        if (data.sync_cursor) syncCursor = data.sync_cursor;

        // /api/sync returns only newly captured timestamps (timestamp > cursor),
        // bounded and oldest-first. They are strictly newer than all existing
        // history, so no membership check or full-array Set is needed — that
        // O(N) work per poll is what previously froze the UI during OCR backlogs.
        const incoming = data.timestamps || [];
        if (incoming.length > 0) {
          const newest = incoming.slice().reverse();  // oldest-first -> newest-first
          timestamps = [...newest, ...timestamps];
          slider.max = timestamps.length - 1;
          showSyncIndicator(newest.length);

          if (wasAtLatest) {
             slider.value = slider.max;
             updateDisplay(timestamps[0]);
          }

          updateJumpButtonVisibility();
        } else {
          hideSyncIndicator();
        }
      } catch (e) { console.error("Sync failed", e); }
    }

    function showSyncIndicator(count) {
      if (!syncIndicator) {
        syncIndicator = document.createElement('div');
        syncIndicator.style.cssText = 'position:fixed;top:60px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.7);color:#fff;padding:6px 16px;border-radius:20px;font-size:13px;z-index:9999;backdrop-filter:blur(10px);transition:opacity 0.3s;';
        document.body.appendChild(syncIndicator);
      }
      syncIndicator.textContent = `Syncing ${count} new screenshots...`;
      syncIndicator.style.opacity = '1';
    }

    function hideSyncIndicator() {
      if (syncIndicator) {
        syncIndicator.style.opacity = '0';
        setTimeout(() => { if (syncIndicator) syncIndicator.textContent = ''; }, 300);
      }
    }

    function startSync() {
      syncData();
      if (!syncInterval) syncInterval = setInterval(syncData, 2000);
    }

    function stopSync() {
      if (syncInterval) {
        clearInterval(syncInterval);
        syncInterval = null;
      }
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === 'visible') startSync();
      else stopSync();
    });
    window.addEventListener("focus", startSync);
    window.addEventListener("blur", stopSync);
    let currentAbortController = null;

    if (document.visibilityState === 'visible') startSync();
    updateJumpButtonVisibility();
    
    // Update display — single flow: show image immediately, fetch data on-demand
    async function updateDisplay(timestamp) {
      // Moving to a new frame cancels any pending on-demand OCR for the old one.
      clearTimeout(dwellTimer);
      // Image loads immediately (direct URL, no API needed)
      screenshot.src = `/static/${timestamp}.webp`;
      dateEl.textContent = new Date(timestamp / 1000).toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', hour12: true
      });

      // Use cache only if it holds a real OCR result. A frame cached while it
      // was still a not-yet-OCR'd stub has text === null; refetch it so the OCR
      // text shows up once the worker has processed it (replaces the old bulk
      // cache-invalidation that flooded /api/sync).
      const cached = entriesData[timestamp];
      if (cached && cached.text !== null && cached.text !== undefined) {
          currentEntry = cached;
          if (screenshot.complete && screenshot.naturalHeight !== 0) renderOverlay();
          screenshot.onload = renderOverlay;
          updateExtractedText();
      } else {
          // Not cached, or cached as a stub: fetch on-demand
          currentEntry = null;
          renderOverlay();
          document.getElementById('extractedText').innerHTML = '<span class="text-muted"><i class="bi bi-arrow-clockwise spinner-border spinner-border-sm"></i> Loading...</span>';

          try {
              if (currentAbortController) currentAbortController.abort();
              currentAbortController = new AbortController();

              const res = await fetch(`/api/entry/${timestamp}`, { signal: currentAbortController.signal });
              const data = await res.json();

              if (data.success) {
                  entriesData[timestamp] = data; // cache for next visit

                  // Only update if user is still on this timestamp
                  const currentIdx = timestamps.length - 1 - parseInt(slider.value);
                  if (timestamps[currentIdx] === timestamp) {
                      currentEntry = data;
                      if (screenshot.complete) renderOverlay();
                      screenshot.onload = renderOverlay;
                      updateExtractedText();

                      // Not OCR'd yet (stub): if the user lingers ~1s here, OCR it now.
                      if (!data.text) {
                          clearTimeout(dwellTimer);
                          dwellTimer = setTimeout(() => ocrOnDwell(timestamp), 1000);
                      }
                  }
              }
          } catch (e) {
              if (e.name === 'AbortError') return;
              console.error("Fetch error", e);
          }
      }

      // Prefetch neighbors for smooth scrolling
      clearTimeout(prefetchTimeout);
      prefetchTimeout = setTimeout(() => {
          const currentIdx = timestamps.length - 1 - parseInt(slider.value);
          prefetchNeighbors(currentIdx);
      }, 500);
    }
    
    // Slider
    slider.addEventListener('input', () => {
      const idx = timestamps.length - 1 - parseInt(slider.value);
      
      if (isDeleteMode) {
        deleteEndIndex = idx;
        updateDeleteInfo();
      }
      
      updateDisplay(timestamps[idx]);
      updateJumpButtonVisibility();
    });

    // Prefetching Logic
    const fetchingMetadata = new Set();
    let prefetchTimeout = null;

    // On-demand OCR: if the user lingers ~1s on a not-yet-OCR'd frame, run OCR
    // for it on the fly (persisted + searchable) and refresh the text/overlay.
    let dwellTimer = null;
    async function ocrOnDwell(timestamp) {
      const idx = timestamps.length - 1 - parseInt(slider.value);
      if (timestamps[idx] !== timestamp) return;  // moved away before dwell fired
      const extractedEl = document.getElementById('extractedText');
      extractedEl.innerHTML = '<span class="text-muted"><i class="bi bi-arrow-clockwise spin-anim"></i> Reading text…</span>';
      try {
        const res = await fetch(`/api/ocr-now/${timestamp}`, { method: 'POST' });
        const data = await res.json();
        if (!data.success) { updateExtractedText(); return; }
        const base = entriesData[timestamp] || { success: true, timestamp: timestamp, ai_text: null, ai_words_coords: [] };
        base.text = data.text || '';
        base.words_coords = data.words_coords || [];
        entriesData[timestamp] = base;  // cache so we don't OCR it again
        const idx2 = timestamps.length - 1 - parseInt(slider.value);
        if (timestamps[idx2] === timestamp) {
          currentEntry = base;
          renderOverlay();
          updateExtractedText();
        }
      } catch (e) {
        console.error('on-demand OCR failed', e);
        updateExtractedText();
      }
    }

    async function prefetchNeighbors(currentIndex) {
        const PREFETCH_RANGE = 20; // Fetch 20 frames before and after
        const neighbors = [];

        // Include current index to ensure it gets loaded if updateDisplay failed/aborted
        neighbors.push(currentIndex);

        for (let i = 1; i <= PREFETCH_RANGE; i++) {
            // Check future (more recent)
            if (currentIndex - i >= 0) neighbors.push(currentIndex - i);
            // Check past (older)
            if (currentIndex + i < timestamps.length) neighbors.push(currentIndex + i);
        }

        // Filter: only fetch what we don't have and aren't already fetching
        const toFetch = neighbors.filter(idx => {
            const ts = timestamps[idx];
            return !entriesData[ts] && !fetchingMetadata.has(ts);
        });

        // Loop and fetch sequentially (or parallel-limit) to avoid flooding
        for (const idx of toFetch) {
            const ts = timestamps[idx];
            fetchingMetadata.add(ts);
            
            // We fetch without await inside the loop to allow some parallelism, 
            // but we might want to respect browser limits. 
            // For now, let's just trigger them.
            fetch(`/api/entry/${ts}`)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        entriesData[ts] = data;
                        // If user happened to scroll to this one while it was loading in background:
                        const currentSliderVal = parseInt(slider.value);
                        const currentIdx = timestamps.length - 1 - currentSliderVal;
                        if (timestamps[currentIdx] === ts) {
                            currentEntry = data;
                            if (screenshot.complete) renderOverlay();
                            updateExtractedText();
                        }
                    }
                })
                .catch(err => console.error("Prefetch error", err))
                .finally(() => {
                    fetchingMetadata.delete(ts);
                });
        }
    }
    
    // Trackpad scrubbing
    let accDelta = 0, isScrolling = false, scrollTimeout = null;
    document.addEventListener('wheel', e => {
      if (Math.abs(e.deltaX) > 0) e.preventDefault();
    }, {passive: false, capture: true});
    
    // Global Key Handler
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
         let handled = false;

         // In the text dialog: Esc closes the find bar first, then the dialog
         const findBar = document.getElementById('dialogFind');
         if (findBar && findBar.classList.contains('show')) {
           closeDialogFind();
           handled = true;
         } else if (isTextDialogOpen()) {
           closeTextDialog();
           handled = true;
         }

         // Close the command palette if open
         if (isPaletteOpen()) {
           hidePaletteActions();
           searchInput.blur();
           handled = true;
         }

         // Close the timeline menu if open
         const tlMenu = document.getElementById('timelineMenu');
         if (tlMenu && tlMenu.classList.contains('show')) {
           tlMenu.classList.remove('show');
           handled = true;
         }

         // Close AI config if open
         const aiConfig = document.getElementById('aiConfigModal');
         if (aiConfig && aiConfig.classList.contains('show')) {
           closeAIConfig();
           handled = true;
         }
         
         // Close text popup if open
         const textPopup = document.getElementById('textPopup');
         if (textPopup && textPopup.classList.contains('show')) {
           closeTextPopup();
           handled = true;
         }

         // Close search results if open
         if (searchResults && searchResults.classList.contains('show')) {
           resetSearchState();
           searchInput.value = '';
           document.getElementById('searchIcon').style.display = 'block';
           document.getElementById('searchClear').style.display = 'none';
           searchInput.focus();
           handled = true;
         }
         
         // Exit delete mode if active
         if (isDeleteMode) {
           exitDeleteMode();
           handled = true;
         }

         // If nothing was open, hide the app window
         if (!handled && window.electronAPI) {
           window.electronAPI.hideWindow();
         }
      }
    });
    
    screenshotArea.addEventListener('wheel', e => {
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && Math.abs(e.deltaX) > 0) {
        e.preventDefault();
        e.stopPropagation();
        
        accDelta += e.deltaX * 0.08;
        const frames = Math.floor(Math.abs(accDelta));
        
        if (frames >= 1) {
          const dir = accDelta > 0 ? 1 : -1;
          let newVal = parseInt(slider.value) + (dir * frames);
          accDelta = accDelta % 1;
          newVal = Math.max(0, Math.min(timestamps.length - 1, newVal));
          
          if (newVal !== parseInt(slider.value)) {
            slider.value = newVal;
            const idx = timestamps.length - 1 - slider.value;
            
            if (isDeleteMode) {
              deleteEndIndex = idx;
              updateDeleteInfo();
            }
            
            updateJumpButtonVisibility();

            const ts = timestamps[idx];
            
            if (!isScrolling) isScrolling = true;
            
            dateEl.textContent = new Date(ts / 1000).toLocaleString('en-US', {
              month: 'short', day: 'numeric', year: 'numeric',
              hour: 'numeric', minute: '2-digit', hour12: true
            });
            screenshot.src = `/static/${ts}.webp`;
            currentEntry = entriesData[ts];
          }
          
          clearTimeout(scrollTimeout);
          scrollTimeout = setTimeout(() => {
            isScrolling = false;
            renderOverlay();
            
            // Trigger prefetch for neighbors with debounce
            clearTimeout(prefetchTimeout);
            prefetchTimeout = setTimeout(() => {
                const idx = timestamps.length - 1 - slider.value;
                prefetchNeighbors(idx);
            }, 500);
            
          }, 300);
        }
      }
    }, {passive: false});
    
    // Arrow keys
    document.addEventListener('keydown', e => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        const dir = e.key === 'ArrowRight' ? 1 : -1;
        let newVal = parseInt(slider.value) + dir;
        newVal = Math.max(0, Math.min(timestamps.length - 1, newVal));
        if (newVal !== parseInt(slider.value)) {
          slider.value = newVal;
          const idx = timestamps.length - 1 - slider.value;
          
          if (isDeleteMode) {
            deleteEndIndex = idx;
            updateDeleteInfo();
          }
          
          updateDisplay(timestamps[idx]);
          
          // Trigger prefetch for neighbors with debounce
          clearTimeout(prefetchTimeout);
          prefetchTimeout = setTimeout(() => {
              prefetchNeighbors(idx);
          }, 500);
        }
      }
    });
    // Render overlay
    function groupWords(words) {
      if (!words || words.length === 0) return [];
      const lines = [];
      let line = [words[0]];
      for (let i = 1; i < words.length; i++) {
        const p = words[i-1], c = words[i];
        const vd = Math.abs(c.y1 - p.y1);
        const ah = (c.y2 - c.y1 + p.y2 - p.y1) / 2;
        if (vd < ah * 0.5) line.push(c);
        else { lines.push(line); line = [c]; }
      }
      lines.push(line);
      
      const blocks = [];
      let block = [lines[0]];
      for (let i = 1; i < lines.length; i++) {
        const pl = lines[i-1], cl = lines[i];
        const pmy = Math.max(...pl.map(w => w.y2));
        const cmy = Math.min(...cl.map(w => w.y1));
        const gap = cmy - pmy;
        const alh = ((Math.max(...pl.map(w => w.y2)) - Math.min(...pl.map(w => w.y1))) +
                     (Math.max(...cl.map(w => w.y2)) - Math.min(...cl.map(w => w.y1)))) / 2;
        if (gap < alh * 1.5) block.push(cl);
        else { blocks.push(block); block = [cl]; }
      }
      blocks.push(block);
      
      return blocks.map(b => {
        const all = b.flat();
        return {
          x1: Math.min(...all.map(w => w.x1)),
          y1: Math.min(...all.map(w => w.y1)),
          x2: Math.max(...all.map(w => w.x2)),
          y2: Math.max(...all.map(w => w.y2)),
          text: b.map(l => l.map(w => w.text).join(' ')).join('\\n')
        };
      });
    }
    
    async function renderOverlay() {
      textOverlay.innerHTML = '';
      if (!currentEntry || !currentEntry.words_coords || currentEntry.words_coords.length === 0) return;
      
      const w = screenshot.clientWidth;
      const h = screenshot.clientHeight;
      textOverlay.style.width = w + 'px';
      textOverlay.style.height = h + 'px';
      
      const blocks = groupWords(currentEntry.words_coords);
      const positions = [];
      const minDist = 40;
      
      blocks.forEach(block => {
        const bw = (block.x2 - block.x1) * w;
        const bh = (block.y2 - block.y1) * h;
        let left = block.x1 * w + bw/2 - 16;
        let top = block.y1 * h + bh/2 - 16;
        
        let overlapping = true, attempts = 0;
        while (overlapping && attempts < 10) {
          overlapping = false;
          for (const pos of positions) {
            const dist = Math.sqrt(Math.pow(left - pos.left, 2) + Math.pow(top - pos.top, 2));
            if (dist < minDist) {
              overlapping = true;
              if (attempts === 0) { left = block.x1 * w; top = block.y1 * h; }
              else if (attempts === 1) { left = block.x2 * w - 32; top = block.y1 * h; }
              else if (attempts === 2) { left = block.x1 * w; top = block.y2 * h - 32; }
              else if (attempts === 3) { left = block.x2 * w - 32; top = block.y2 * h - 32; }
              else { left += (Math.random() - 0.5) * 20; top += (Math.random() - 0.5) * 20; }
              break;
            }
          }
          attempts++;
        }
        
        positions.push({left, top});
        const icon = document.createElement('div');
        icon.className = 'text-icon';
        icon.innerHTML = '<i class="bi bi-file-text"></i>';
        icon.style.left = left + 'px';
        icon.style.top = top + 'px';
        icon.onclick = () => showTextPopup(block.text);
        textOverlay.appendChild(icon);
      });
    }
    
    // Text popup
    function showTextPopup(text) {
      document.getElementById('popupText').textContent = text;
      document.getElementById('textPopup').classList.add('show');
      document.getElementById('textPopupOverlay').classList.add('show');
    }
    
    function closeTextPopup() {
      document.getElementById('textPopup').classList.remove('show');
      document.getElementById('textPopupOverlay').classList.remove('show');
    }
    
    function copyPopupText() {
      const text = document.getElementById('popupText').textContent;
      navigator.clipboard.writeText(text).then(() => alert('Copied!'));
    }
    
    const searchIcon = document.getElementById('searchIcon');
    const searchClear = document.getElementById('searchClear');
    const searchWrapper = document.getElementById('searchWrapper');
    const searchLive = document.getElementById('searchLive');

    // ---- Search: paginated (load-more), progressive, keyboard-navigable ----
    const PAGE_SIZE = 30;
    let searchState = { q: '', offset: 0, total: 0, hasMore: false, loading: false };
    let selectedIndex = -1;   // -1 = input focused, no card selected
    let searchReqId = 0;      // monotonic guard: an aborted search never touches a newer one's UI
    let resultsGrid = null;

    function announce(msg) { if (searchLive) searchLive.textContent = msg; }
    function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
    function fmtTime(ts) {
      return new Date(ts / 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
    // Treat a-z, 0-9 and any non-ASCII (accented letters) as word chars — used for
    // whole-word match boundaries without regex (avoids escaping pitfalls + handles accents).
    function isWordChar(ch) {
      if (!ch) return false;
      const c = ch.toLowerCase();
      return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c.charCodeAt(0) > 127;
    }
    // Highlight query terms in `snippet` into `el` using textNodes + <mark>
    // (XSS-safe; offsets computed in JS so no codepoint/UTF-16 drift).
    function renderSnippet(el, snippet, terms) {
      el.textContent = '';
      if (!snippet) return;
      const toks = terms.filter(t => t.length >= 2).map(t => t.toLowerCase());
      if (!toks.length) { el.textContent = snippet; return; }
      const low = snippet.toLowerCase();
      const ranges = [];
      for (const t of toks) {
        let from = 0, idx;
        while ((idx = low.indexOf(t, from)) !== -1) {
          if (!isWordChar(snippet[idx - 1]) && !isWordChar(snippet[idx + t.length])) ranges.push([idx, idx + t.length]);
          from = idx + t.length;
        }
      }
      if (!ranges.length) { el.textContent = snippet; return; }
      ranges.sort((a, b) => a[0] - b[0]);
      const merged = [ranges[0]];
      for (let k = 1; k < ranges.length; k++) {
        const last = merged[merged.length - 1];
        if (ranges[k][0] <= last[1]) last[1] = Math.max(last[1], ranges[k][1]);
        else merged.push(ranges[k]);
      }
      let pos = 0;
      for (const [s, e] of merged) {
        if (s > pos) el.appendChild(document.createTextNode(snippet.slice(pos, s)));
        const mk = document.createElement('mark'); mk.className = 'search-mark'; mk.textContent = snippet.slice(s, e);
        el.appendChild(mk); pos = e;
      }
      if (pos < snippet.length) el.appendChild(document.createTextNode(snippet.slice(pos)));
    }

    function resetSearchState() {
      if (searchController) searchController.abort();
      searchReqId++;   // invalidate any already-resolved-but-not-yet-handled response
      searchState = { q: '', offset: 0, total: 0, hasMore: false, loading: false };
      selectedIndex = -1; resultsGrid = null;
      searchWrapper.classList.remove('searching');
      searchResults.classList.remove('show');
      searchResults.innerHTML = '';
      searchInput.setAttribute('aria-expanded', 'false');
      searchInput.removeAttribute('aria-activedescendant');
      announce('');
    }

    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      const q = searchInput.value.trim();
      if (searchInput.value.length > 0) { searchIcon.style.display = 'none'; searchClear.style.display = 'block'; }
      else { searchIcon.style.display = 'block'; searchClear.style.display = 'none'; }
      if (!q) { resetSearchState(); return; }
      // 500ms debounce: a cold scan is ~uncancellable server-side, so don't fire too eagerly.
      searchTimeout = setTimeout(() => performSearch(q, { reset: true }), 500);
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchInput.dispatchEvent(new Event('input'));
      searchInput.focus();
    });

    function searchUrl(q, offset) {
      const p = new URLSearchParams({ q: q, limit: String(PAGE_SIZE), offset: String(offset) });
      return '/api/search?' + p.toString();
    }

    async function performSearch(q, opts) {
      const reset = !opts || opts.reset !== false;
      if (searchController) searchController.abort();
      searchController = new AbortController();
      const signal = searchController.signal;
      const myReq = ++searchReqId;

      if (reset) {
        searchState = { q: q, offset: 0, total: 0, hasMore: false, loading: true };
        selectedIndex = -1;
        searchInput.removeAttribute('aria-activedescendant');  // old selected card is gone
        renderSkeletons();
        searchResults.classList.add('show');
        searchInput.setAttribute('aria-expanded', 'true');
        searchWrapper.classList.add('searching');
        announce('Searching');
      } else {
        searchState.loading = true;
      }

      try {
        const resp = await fetch(searchUrl(q, searchState.offset), { signal: signal });
        const data = await resp.json();
        if (myReq !== searchReqId) return;        // a newer search owns the UI now
        renderResults(data, { append: !reset });
      } catch (err) {
        if (err.name === 'AbortError' || myReq !== searchReqId) return;
        if (reset) renderError(q); else renderLoadMoreError();
      } finally {
        if (myReq === searchReqId) {
          searchState.loading = false;
          searchWrapper.classList.remove('searching');
        }
      }
    }

    function renderSkeletons() {
      searchResults.innerHTML =
        '<div class="search-header"><span class="search-count">Searching…</span></div>' +
        '<div class="results-grid" id="resultsGrid" role="listbox" aria-label="Search results"></div>';
      resultsGrid = document.getElementById('resultsGrid');
      for (let i = 0; i < 10; i++) { const s = document.createElement('div'); s.className = 'skeleton-card'; resultsGrid.appendChild(s); }
    }

    function renderResults(data, opts) {
      const append = opts && opts.append;
      searchState.total = data.total || 0;
      searchState.hasMore = !!data.has_more;
      const list = data.results || [];
      searchState.offset = (data.offset || 0) + list.length;
      const terms = searchState.q.toLowerCase().split(' ').filter(Boolean);

      if (!append) {
        searchResults.innerHTML =
          '<div class="search-header"><span class="search-count"></span></div>' +
          '<div class="results-grid" id="resultsGrid" role="listbox" aria-label="Search results"></div>';
        resultsGrid = document.getElementById('resultsGrid');
        selectedIndex = -1;
        searchInput.removeAttribute('aria-activedescendant');
        const count = searchState.total;
        searchResults.querySelector('.search-count').textContent = count ? (count + ' result' + (count === 1 ? '' : 's')) : '';
        if (list.length === 0) { renderEmpty(searchState.q); announce('No results'); return; }
        announce(count + ' results');
      }

      appendCards(list, terms);
      renderLoadMore();
      if (append) announce('Loaded ' + list.length + ' more — ' + searchState.offset + ' of ' + searchState.total + ' shown');
    }

    function appendCards(list, terms) {
      if (!resultsGrid) return;
      for (const r of list) {
        const card = document.createElement('div');
        card.className = 'result-card'; card.id = 'rc-' + r.timestamp;
        card.setAttribute('role', 'option'); card.tabIndex = -1; card.dataset.ts = r.timestamp;
        const img = document.createElement('img');
        img.loading = 'lazy'; img.decoding = 'async'; img.alt = '';
        img.onload = () => img.classList.add('loaded');
        img.src = '/static/' + r.timestamp + '.webp';
        const time = document.createElement('div'); time.className = 'result-time'; time.textContent = fmtTime(r.timestamp);
        const snip = document.createElement('div'); snip.className = 'result-snippet';
        renderSnippet(snip, r.snippet || '', terms);
        card.appendChild(img); card.appendChild(time); card.appendChild(snip);
        resultsGrid.appendChild(card);
      }
    }

    function renderLoadMore() {
      let btn = document.getElementById('loadMore');
      if (searchState.hasMore) {
        if (!btn) { btn = document.createElement('button'); btn.id = 'loadMore'; btn.className = 'load-more'; searchResults.appendChild(btn); }
        const left = Math.max(0, searchState.total - searchState.offset);
        btn.disabled = false; btn.textContent = 'Load more' + (left ? ' (' + left + ' left)' : '');
      } else if (btn) { btn.remove(); }
    }

    async function loadMore() {
      if (!searchState.hasMore || searchState.loading) return;
      const btn = document.getElementById('loadMore');
      if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
      await performSearch(searchState.q, { reset: false });
    }

    function renderLoadMoreError() {
      const btn = document.getElementById('loadMore');
      if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
    }

    function renderEmpty(q) {
      searchResults.innerHTML =
        '<div class="search-empty"><i class="bi bi-search" style="font-size:28px;opacity:0.5"></i>' +
        '<p style="margin:12px 0 4px">No results for "' + escapeHtml(q) + '"</p>' +
        '<p style="font-size:12px;opacity:0.7">Search reads on-screen text (OCR). Try fewer or different words.</p></div>';
    }

    function renderError(q) {
      searchResults.innerHTML =
        '<div class="search-error"><i class="bi bi-exclamation-triangle" style="font-size:24px;opacity:0.6"></i>' +
        '<p style="margin:12px 0">Search failed</p><button class="search-retry" id="searchRetry">Retry</button></div>';
      announce('Search failed');
      const rb = document.getElementById('searchRetry');
      if (rb) rb.onclick = () => performSearch(q, { reset: true });
    }

    // Keyboard navigation across the results grid (combobox + listbox pattern).
    function gridColumns() {
      if (!resultsGrid) return 1;
      const tmpl = getComputedStyle(resultsGrid).gridTemplateColumns.split(' ').filter(Boolean).length;
      if (tmpl >= 1) return tmpl;
      const card = resultsGrid.querySelector('.result-card');
      if (card) return Math.max(1, Math.round(resultsGrid.clientWidth / card.getBoundingClientRect().width));
      return 1;
    }
    function cardEls() { return resultsGrid ? Array.from(resultsGrid.querySelectorAll('.result-card')) : []; }
    function clearSelection() {
      cardEls().forEach(c => c.classList.remove('selected'));
      selectedIndex = -1;
      searchInput.removeAttribute('aria-activedescendant');
    }
    function selectCard(i) {
      const cs = cardEls(); if (!cs.length) return;
      i = Math.max(0, Math.min(i, cs.length - 1));
      cs.forEach((c, j) => c.classList.toggle('selected', j === i));
      selectedIndex = i;
      const c = cs[i];
      c.scrollIntoView({ block: 'nearest' });
      searchInput.setAttribute('aria-activedescendant', c.id);   // set only now that the card is in the DOM
    }
    searchInput.addEventListener('keydown', async (e) => {
      if (!searchResults.classList.contains('show')) return;
      const cs = cardEls(); const cols = gridColumns();
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (selectedIndex === -1) { if (cs.length) selectCard(0); }
        else if (selectedIndex + cols < cs.length) selectCard(selectedIndex + cols);
        else if (searchState.hasMore) { await loadMore(); selectCard(selectedIndex + cols); }
      } else if (e.key === 'ArrowUp') {
        if (selectedIndex === -1) return;
        e.preventDefault();
        if (selectedIndex - cols >= 0) selectCard(selectedIndex - cols);
        else clearSelection();
      } else if (e.key === 'ArrowRight') {
        if (selectedIndex >= 0) { e.preventDefault(); selectCard(selectedIndex + 1); }
      } else if (e.key === 'ArrowLeft') {
        if (selectedIndex > 0) { e.preventDefault(); selectCard(selectedIndex - 1); }
      } else if (e.key === 'Home') {
        if (cs.length) { e.preventDefault(); selectCard(0); }
      } else if (e.key === 'End') {
        if (cs.length) { e.preventDefault(); if (searchState.hasMore) await loadMore(); selectCard(cardEls().length - 1); }
      } else if (e.key === 'Enter') {
        if (selectedIndex >= 0 && cs[selectedIndex]) { e.preventDefault(); goToTimestamp(parseInt(cs[selectedIndex].dataset.ts)); }
      }
      // Escape is handled by the global key handler (calls resetSearchState).
    });

    // Delegated activation: card click and the load-more button.
    searchResults.addEventListener('click', (e) => {
      const card = e.target.closest('.result-card');
      if (card && card.dataset.ts) { goToTimestamp(parseInt(card.dataset.ts)); return; }
      if (e.target.closest('#loadMore')) loadMore();
    });

    function goToTimestamp(ts) {
      const idx = timestamps.indexOf(ts);
      if (idx !== -1) {
        slider.value = timestamps.length - 1 - idx;
      }
      // Show the frame even if it isn't in the timeline array yet (e.g. captured
      // after page load, before the next sync poll) — never a silent no-op.
      updateDisplay(ts);
      resetSearchState();
      searchInput.value = '';
      searchIcon.style.display = 'block';
      searchClear.style.display = 'none';
    }
    
    // Sidebar
    // ===== Text dialog (current frame OCR text + actions) =====
    let _aiRunning = false;                 // AI transcription in progress
    let _findCount = 0, _findCurrent = 0, _copyT = null;

    function openTextDialog(aiMode) {
      hidePaletteActions();
      closeDialogFind();
      if (aiMode) {
        showOCRMode('ai');
        if (!(currentEntry && currentEntry.ai_text)) runAIOCR();
      }
      refreshTextDialog();
      document.getElementById('textDialogScrim').classList.add('show');
      const dlg = document.getElementById('textDialog');
      dlg.classList.add('show');
      dlg.setAttribute('aria-hidden', 'false');
    }
    function closeTextDialog() {
      closeDialogFind();
      document.getElementById('textDialog').classList.remove('show');
      document.getElementById('textDialogScrim').classList.remove('show');
      document.getElementById('textDialog').setAttribute('aria-hidden', 'true');
    }
    function isTextDialogOpen() {
      const d = document.getElementById('textDialog');
      return !!(d && d.classList.contains('show'));
    }
    function formatEntryTime(ts) {
      try { return new Date(ts / 1000).toLocaleString(); } catch (e) { return ''; }
    }
    // The text currently shown: '' = frame has no base text, null = AI source but no
    // transcription yet, otherwise the string.
    function _dialogShownText() {
      if (!currentEntry) return '';
      if (currentOCRMode === 'ai') return currentEntry.ai_text || null;
      return currentEntry.text || '';
    }
    // Sync ALL dialog chrome + body to the current frame, source, and AI state.
    function refreshTextDialog() {
      const isAi = currentOCRMode === 'ai';
      const aiDone = !!(currentEntry && currentEntry.ai_text);
      const tsEl = document.getElementById('textDialogTs');
      if (tsEl) tsEl.textContent = currentEntry ? formatEntryTime(currentEntry.timestamp) : '';
      const sw = document.getElementById('sourceSwitch');
      if (sw) sw.classList.toggle('on', isAi);
      const base = document.getElementById('modeLabelBase'), ai = document.getElementById('modeLabelAi');
      if (base) base.classList.toggle('active', !isAi);
      if (ai) ai.classList.toggle('active', isAi);
      const runBtn = document.getElementById('btnRunAI');
      if (runBtn) {
        runBtn.classList.toggle('running', _aiRunning);
        const spin = runBtn.querySelector('.ai-spinner'), star = runBtn.querySelector('.ai-star'), lbl = runBtn.querySelector('.run-label');
        if (spin) spin.style.display = _aiRunning ? 'inline-block' : 'none';
        if (star) star.style.display = _aiRunning ? 'none' : 'inline';
        if (lbl) lbl.textContent = _aiRunning ? 'Transcribing…' : (aiDone ? 'Re-run' : 'Extract with AI');
      }
      const copyBtn = document.getElementById('btnCopyText');
      if (copyBtn) copyBtn.classList.toggle('disabled', !_dialogShownText());
      const banner = document.getElementById('textDialogBanner');
      if (banner) banner.classList.toggle('show', isAi && aiDone);
      const findBtn = document.getElementById('btnFind'), findBar = document.getElementById('dialogFind');
      if (findBtn && findBar) findBtn.classList.toggle('active', findBar.classList.contains('show'));
      renderDialogBody();
    }
    // Render the body: empty / AI-not-run states, or the text (with find highlights).
    function renderDialogBody() {
      const body = document.getElementById('extractedText');
      if (!body) return;
      const isAi = currentOCRMode === 'ai';
      const aiDone = !!(currentEntry && currentEntry.ai_text);
      body.classList.toggle('ai-size', isAi && aiDone);
      const t = _dialogShownText();
      if (t === null) {
        body.innerHTML = '<div class="td-state"><i class="bi bi-stars" style="font-size:24px;color:#7fc0ff;"></i>' +
          '<span>No AI transcription for this frame yet.</span>' +
          '<span class="td-state-hint">Use “Extract with AI” above to create one.</span></div>';
        return;
      }
      if (!t) {
        body.innerHTML = '<div class="td-state"><i class="bi bi-justify-left" style="font-size:26px;"></i>' +
          '<span>No text for this frame.</span></div>';
        return;
      }
      const findBar = document.getElementById('dialogFind');
      const q = (findBar && findBar.classList.contains('show')) ? _dialogFindQuery().toLowerCase() : '';
      if (!q || _findCount === 0) { body.textContent = t; return; }
      const hay = t.toLowerCase();
      let html = '', last = 0, m = 0, idx = hay.indexOf(q);
      while (idx !== -1) {
        html += _escHtml(t.slice(last, idx));
        html += '<mark class="find-hl' + (m === _findCurrent ? ' current' : '') + '">' + _escHtml(t.substr(idx, q.length)) + '</mark>';
        last = idx + q.length; m++;
        idx = hay.indexOf(q, last);
      }
      html += _escHtml(t.slice(last));
      body.innerHTML = html;
      _scrollToCurrentMark();
    }
    function _scrollToCurrentMark() {
      const c = document.getElementById('extractedText');
      const cur = c.querySelector('mark.find-hl.current');
      if (!cur) return;
      const top = cur.offsetTop;  // relative to #extractedText (position:relative)
      if (top < c.scrollTop + 16 || top > c.scrollTop + c.clientHeight - 48) {
        c.scrollTop = Math.max(0, top - c.clientHeight / 2);
      }
    }

    // ===== Find-in-text inside the dialog (Cmd/Ctrl-F) =====
    function _escHtml(s) { return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
    function _dialogFindQuery() { const i = document.getElementById('dialogFindInput'); return i ? i.value.trim() : ''; }
    function openDialogFind() {
      if (!isTextDialogOpen()) return;
      document.getElementById('dialogFind').classList.add('show');
      document.getElementById('btnFind').classList.add('active');
      const inp = document.getElementById('dialogFindInput');
      inp.focus(); inp.select();
      dialogFindRun();
    }
    function closeDialogFind() {
      const bar = document.getElementById('dialogFind');
      if (bar) bar.classList.remove('show');
      const fb = document.getElementById('btnFind');
      if (fb) fb.classList.remove('active');
      const inp = document.getElementById('dialogFindInput');
      if (inp) inp.value = '';
      _findCount = 0; _findCurrent = 0;
      const cnt = document.getElementById('dialogFindCount');
      if (cnt) cnt.textContent = '';
      renderDialogBody();
    }
    function dialogFindRun() {
      const q = _dialogFindQuery().toLowerCase();
      const t = _dialogShownText();
      _findCount = 0; _findCurrent = 0;
      if (q && t) {
        const hay = t.toLowerCase(); let i = 0;
        while ((i = hay.indexOf(q, i)) !== -1) { _findCount++; i += q.length; }
      }
      _updateFindCounter();
      renderDialogBody();
    }
    function dialogFindStep(dir) {
      if (_findCount === 0) return;
      _findCurrent = (_findCurrent + dir + _findCount) % _findCount;
      _updateFindCounter();
      renderDialogBody();
    }
    function _updateFindCounter() {
      const el = document.getElementById('dialogFindCount');
      if (!el) return;
      const q = _dialogFindQuery();
      el.textContent = q ? (_findCount ? (_findCurrent + 1) + '/' + _findCount : '0/0') : '';
    }
    function dialogFindKey(e) {
      if (e.key === 'Enter') { e.preventDefault(); dialogFindStep(e.shiftKey ? -1 : 1); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeDialogFind(); }
    }

    // ===== Command palette: global actions under the search bar =====
    function showPaletteActions() { document.getElementById('paletteActions').classList.add('show'); }
    function hidePaletteActions() { document.getElementById('paletteActions').classList.remove('show'); }
    function isPaletteOpen() { return document.getElementById('paletteActions').classList.contains('show'); }
    function paletteAction(which) {
      hidePaletteActions();
      try { searchInput.blur(); } catch (e) {}
      if (which === 'text') openTextDialog(false);
      else if (which === 'ai') openTextDialog(true);
      else if (which === 'settings') openSettings();
      else if (which === 'hide') hideAppWindow();
      else if (which === 'quit') quitAppFromMenu();
    }
    // Searchbar doubles as a command palette: focusing it (empty) shows global
    // actions; typing hands off to the existing history search.
    searchInput.addEventListener('focus', () => { if (!searchInput.value.trim()) showPaletteActions(); });
    searchInput.addEventListener('input', () => { searchInput.value.trim() ? hidePaletteActions() : showPaletteActions(); });
    searchInput.addEventListener('blur', () => { setTimeout(hidePaletteActions, 120); });
    // Cmd/Ctrl-K focuses the searchbar (opens the palette); again or Esc closes it.
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (document.activeElement === searchInput || isPaletteOpen()) {
          hidePaletteActions(); searchInput.blur();
        } else {
          searchInput.focus();
          if (!searchInput.value.trim()) showPaletteActions();
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f' && isTextDialogOpen()) {
        e.preventDefault();
        openDialogFind();
      }
    });

    let currentOCRMode = 'basic';
    let aiConfig = null;
    
    fetch('/api/config').then(r => r.json()).then(c => aiConfig = c);
    
    function toggleOCRMode() {
      currentOCRMode = currentOCRMode === 'ai' ? 'basic' : 'ai';
      updateExtractedText();
    }

    function showOCRMode(mode) {
      currentOCRMode = mode;
      updateExtractedText();
    }

    async function runAIOCR() {
      if (!currentEntry) {
        showToast('No screenshot selected', 'error');
        return;
      }

      _aiRunning = true;
      refreshTextDialog();  // shows spinner + "Transcribing…" on the Run-AI button

      // Show info toast about processing time
      showToast('AI OCR is processing... This may take 10-30 seconds as the AI analyzes the entire screenshot.', 'info');

      try {
        const configResp = await fetch('/api/config?full=true');
        const fullConfig = await configResp.json();

        if (!fullConfig.api_key || fullConfig.api_key === '***' || fullConfig.api_key === '') {
          showToast('Please configure AI settings first', 'error');
          showAIConfig();
          return;
        }

        const response = await fetch('/api/ai-ocr', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            timestamp: currentEntry.timestamp,
            provider: fullConfig.provider || 'gemini',
            api_key: fullConfig.api_key
          })
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error || 'AI OCR failed');
        }

        const result = await response.json();
        currentEntry.ai_text = result.text;
        currentEntry.ai_words_coords = result.words_coords;
        entriesData[currentEntry.timestamp].ai_text = result.text;
        entriesData[currentEntry.timestamp].ai_words_coords = result.words_coords;

        currentOCRMode = 'ai';
        showToast('AI OCR completed successfully! ✨', 'success');
      } catch (error) {
        showToast('AI OCR error: ' + error.message, 'error');
      } finally {
        _aiRunning = false;
        refreshTextDialog();
      }
    }
    
    // Toast notifications
    function showToast(message, type = 'info') {
      const container = document.getElementById('toastContainer');
      const toast = document.createElement('div');
      toast.className = `toast ${type}`;
      
      const iconMap = {
        success: 'bi-check-circle-fill',
        error: 'bi-x-circle-fill',
        info: 'bi-info-circle-fill'
      };
      
      toast.innerHTML = `
        <i class="bi ${iconMap[type]} toast-icon"></i>
        <div class="toast-content">${message}</div>
        <button class="toast-close" onclick="this.parentElement.remove()">&times;</button>
      `;
      
      container.appendChild(toast);
      setTimeout(() => toast.remove(), 4000);
    }
    
    // AI Config Modal
    function showAIConfig() {
      const modal = document.getElementById('configModalOverlay');
      
      // Load current config
      if (aiConfig) {
        document.getElementById('aiProvider').value = aiConfig.provider || 'gemini';
      }
      
      modal.classList.add('show');
    }
    
    function closeAIConfig() {
      document.getElementById('configModalOverlay').classList.remove('show');
      document.getElementById('aiApiKey').value = '';
    }
    
    async function saveAIConfig() {
      const provider = document.getElementById('aiProvider').value;
      const apiKey = document.getElementById('aiApiKey').value;
      
      if (!apiKey) {
        showToast('Please enter an API key', 'error');
        return;
      }
      
      try {
        const response = await fetch('/api/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({provider, api_key: apiKey})
        });
        
        if (response.ok) {
          aiConfig = {provider, api_key: '***'};
          closeAIConfig();
          showToast('AI settings saved successfully!', 'success');
        } else {
          showToast('Failed to save settings', 'error');
        }
      } catch (error) {
        showToast('Error: ' + error.message, 'error');
      }
    }
    
    function copyExtractedText() {
      const text = _dialogShownText();
      if (!text) return;
      navigator.clipboard.writeText(text).catch(() => showToast('Failed to copy text', 'error'));
      const lbl = document.querySelector('#btnCopyText .copy-label');
      if (lbl) {
        lbl.textContent = '✓ Copied';
        clearTimeout(_copyT);
        _copyT = setTimeout(() => { lbl.textContent = 'Copy all'; }, 1400);
      }
    }
    
    function copyPopupText() {
      const text = document.getElementById('popupText').textContent;
      navigator.clipboard.writeText(text).then(() => {
        showToast('Text copied to clipboard!', 'success');
      }).catch(() => {
        showToast('Failed to copy text', 'error');
      });
    }
    

    

    // Settings Logic
    function openSettings() {
        document.getElementById('settingsModalOverlay').classList.add('show');
        // Load retention
        fetch('/api/settings/retention')
            .then(r => r.json())
            .then(data => {
                document.getElementById('retentionSelect').value = data.days;
            });
        // Load interval
        fetch('/api/settings/interval')
            .then(r => r.json())
            .then(data => {
                const input = document.getElementById('intervalInput');
                input.value = data.interval;
                checkIntervalWarning(data.interval);
            });
        // Load quality
        fetch('/api/settings/quality')
            .then(r => r.json())
            .then(data => {
                document.getElementById('qualitySelect').value = data.quality;
            });
        // Load OCR cooldown
        fetch('/api/settings/ocr-cooldown')
            .then(r => r.json())
            .then(data => {
                document.getElementById('ocrCooldownInput').value = data.ocr_cooldown;
            });
        // Load OCR compute mode
        fetch('/api/settings/ocr-compute-mode')
            .then(r => r.json())
            .then(data => {
                document.getElementById('ocrComputeModeSelect').value = data.ocr_compute_mode;
            });
        // Load skip incognito
        fetch('/api/settings/skip_incognito')
            .then(r => r.json())
            .then(data => {
                document.getElementById('skipIncognitoCheckbox').checked = data.skip_incognito;
            });
        // Load port
        fetch('/api/settings/port')
            .then(r => r.json())
            .then(data => {
                document.getElementById('portInput').value = data.port;
            });
        // Load Apple Vision setting
        fetch('/api/settings/apple_vision')
          .then(r => r.json())
          .then(d => {
            const section = document.getElementById('ocrEngineSection');
            const cb = document.getElementById('useAppleVisionCheckbox');
            if (d.available) {
              section.style.display = '';
              cb.checked = !!d.enabled;
            } else {
              section.style.display = 'none';
            }
          })
          .catch(e => console.warn('apple_vision settings fetch failed', e));
    }

    function checkIntervalWarning(val) {
        const warning = document.getElementById('intervalWarning');
        if (parseInt(val) < 3) {
            warning.style.display = 'block';
        } else {
            warning.style.display = 'none';
        }
    }

    function closeSettings() {
        document.getElementById('settingsModalOverlay').classList.remove('show');
    }

    function saveSettings() {
        const days = document.getElementById('retentionSelect').value;
        const interval = document.getElementById('intervalInput').value;

        fetch('/api/settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                retention_days: days,
                interval: interval,
                quality: document.getElementById('qualitySelect').value,
                ocr_cooldown: document.getElementById('ocrCooldownInput').value,
                ocr_compute_mode: document.getElementById('ocrComputeModeSelect').value,
                skip_incognito: document.getElementById('skipIncognitoCheckbox').checked,
                port: document.getElementById('portInput').value,
                use_apple_vision: document.getElementById('useAppleVisionCheckbox').checked
            })
        })
        .then(r => r.json())
        .then(result => {
             if(result.success) {
                if (result.restart_required) {
                    showToast('Settings saved. Restart required for port change.', 'warning');
                } else {
                    showToast('Settings saved successfully!', 'success');
                }
                closeSettings();
            } else {
                showToast('Error saving settings: ' + result.error, 'error');
            }
        })

        .catch(err => {
            console.error(err);
            showToast('Failed to save settings', 'error');
        });
    }

    
    // Calendar Logic
    let calendarDate = new Date();
    // Pre-process active days for faster lookup
    const activeDays = new Set();
    const dayToTimestampMap = {}; 
    
    function initCalendar() {
      // Populate active days from timestamps
      timestamps.forEach(ts => {
        const date = new Date(ts / 1000);
        const key = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
        if (!activeDays.has(key)) {
          activeDays.add(key);
          dayToTimestampMap[key] = ts; // Store first timestamp of the day
        }
      });
      renderCalendar();
    }
    
    function toggleCalendar(e) {
      e.stopPropagation();
      const cal = document.getElementById('calendarWrapper');
      if (cal.classList.contains('show')) {
        cal.classList.remove('show');
      } else {
        // Sync calendar to currently viewed date
        const currentTs = timestamps[timestamps.length - 1 - parseInt(slider.value)];
        if (currentTs) {
          calendarDate = new Date(currentTs / 1000);
        }
        renderCalendar();
        cal.classList.add('show');
      }
    }
    
    function prevMonth() {
      calendarDate.setMonth(calendarDate.getMonth() - 1);
      renderCalendar();
    }
    
    function nextMonth() {
      calendarDate.setMonth(calendarDate.getMonth() + 1);
      renderCalendar();
    }
    
    function renderCalendar() {
      const year = calendarDate.getFullYear();
      const month = calendarDate.getMonth();
      
      document.getElementById('calendarTitle').textContent = new Date(year, month).toLocaleString('en-US', { month: 'long', year: 'numeric' });
      
      const grid = document.getElementById('calendarGrid');
      grid.innerHTML = '';
      
      const weekDays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
      weekDays.forEach(day => {
        const el = document.createElement('div');
        el.className = 'calendar-day-header';
        el.textContent = day;
        grid.appendChild(el);
      });
      
      const firstDay = new Date(year, month, 1).getDay();
      const daysInMonth = new Date(year, month + 1, 0).getDate();
      
      // Empty cells for previous month
      for (let i = 0; i < firstDay; i++) {
        grid.appendChild(document.createElement('div'));
      }
      
      // Days
      for (let day = 1; day <= daysInMonth; day++) {
        const el = document.createElement('div');
        const key = `${year}-${month}-${day}`;
        const hasRecording = activeDays.has(key);
        
        let className = 'calendar-day';
        if (hasRecording) {
            className += ' active has-recording';
            // Check if selected
            const currentTs = timestamps[timestamps.length - 1 - parseInt(slider.value)];
            const currentDate = new Date(currentTs / 1000);
            if (currentDate.getDate() === day && currentDate.getMonth() === month && currentDate.getFullYear() === year) {
                className += ' selected';
            }
        }
        
        el.className = className;
        el.textContent = day;
        
        if (hasRecording) {
            el.onclick = () => {
                const targetTs = dayToTimestampMap[key];
                goToTimestamp(targetTs);
                document.getElementById('calendarWrapper').classList.remove('show');
            };
        }
        
        grid.appendChild(el);
      }
    }
    
    // Auto-close calendar when clicking outside
    document.addEventListener('click', (e) => {
        const cal = document.getElementById('calendarWrapper');
        if (cal.classList.contains('show') && !e.target.closest('.calendar-wrapper') && !e.target.closest('.calendar-btn')) {
            cal.classList.remove('show');
        }
    });
    
    // Init Calendar
    initCalendar();

    
    // Update extracted text on change
    function updateExtractedText() {
      // The dialog body (states + text + find highlights) is rendered by refreshTextDialog.
      refreshTextDialog();
    }

    // Electron UI Reset
    if (window.electronAPI) {
      window.electronAPI.onOpenSettings(() => {
          openSettings();
      });
      window.electronAPI.onResetUI(() => {
        // Close the redesigned menu surfaces
        closeTextDialog();
        hidePaletteActions();

        // Close AI config
        closeAIConfig();
        
        // Close text popup
        closeTextPopup();
        
        // Close search results (route through resetSearchState so the combobox
        // ARIA state — aria-expanded / aria-activedescendant — is reset too).
        if (searchResults.classList.contains('show')) {
          resetSearchState();
          searchInput.value = '';
          searchIcon.style.display = 'block';
          searchClear.style.display = 'none';
        }

        // Exit delete mode
        if (isDeleteMode) exitDeleteMode();
      });
    }

    // Init
    updateDisplay(timestamps[0]);
    updateExtractedText();
  </script>
</body>
</html>
    """, timestamps=all_timestamps, entries_dict=entries_dict)


@app.route("/api/entry/<int:timestamp>")
def api_get_entry(timestamp):
    entry = get_entry_by_timestamp(timestamp)
    if entry:
        return jsonify({
            'success': True,
            'id': entry.id,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'words_coords': entry.words_coords,
            'ai_text': entry.ai_text,
            'ai_words_coords': entry.ai_words_coords if entry.ai_words_coords else []
        })
    else:
        return jsonify({'success': False, 'error': 'Entry not found'}), 404


@app.route("/api/ocr-now/<int:timestamp>", methods=["POST"])
def api_ocr_now(timestamp):
    """On-demand OCR for a single frame (dwell-triggered from the timeline).

    Runs the local OCR engine (Apple Vision when enabled) inline, persists the
    text + embedding so the frame becomes searchable, and returns the result.
    No-ops if the frame already has OCR text; 404 if the screenshot is gone.
    """
    entry = get_entry_by_timestamp(timestamp)
    if entry and entry.text:
        return jsonify({'success': True, 'already': True,
                        'text': entry.text, 'words_coords': entry.words_coords})
    try:
        result = ocr_one_frame(timestamp, use_apple_vision=get_use_apple_vision())
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    if result is None:
        return jsonify({'success': False, 'error': 'Screenshot not found'}), 404
    text, words_coords = result
    return jsonify({'success': True, 'text': text, 'words_coords': words_coords})


@app.route("/api/search")
def api_search():
    """API endpoint for search (paginated, cached).

    Query params:
        q: search text (required).
        limit: page size (default 50, capped at 200).
        offset: results to skip for pagination (default 0).
        since/until: optional timestamp window (microseconds).
        app: optional exact app-name filter.

    Returns {results: [{timestamp, app, title, snippet}], total, offset, limit,
    has_more}. The snippet is a centered preview; the client highlights matches.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": [], "total": 0, "offset": 0,
                        "limit": 0, "has_more": False})

    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    try:
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        offset = 0

    def _int_arg(name):
        raw = request.args.get(name)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    since = _int_arg("since")
    until = _int_arg("until")
    app_filter = request.args.get("app") or None

    query_embedding = get_embedding(q)
    result = search_entries_streaming(
        query_embedding, query_text=q,
        limit=limit, offset=offset,
        since=since, until=until, app=app_filter,
    )

    # Reshape each result for the client: a centered, inline snippet (the client
    # re-derives and highlights matches) instead of the raw OCR text.
    result["results"] = [
        {'timestamp': r['timestamp'], 'app': r['app'], 'title': r['title'],
         'snippet': build_snippet(r['text'], q)}
        for r in result["results"]
    ]
    return jsonify(result)


@app.route("/api/sync")
def api_sync():
    """Lightweight sync: returns only newly captured timestamps, bounded.

    Timestamp-based (not updated_at-based) so an OCR backlog — which bumps
    updated_at on many existing rows — never floods this poll. The payload is
    capped at `limit` and drains oldest-first via the returned cursor. Stale
    cached OCR text is refreshed lazily on view; entry data is fetched on-demand
    via /api/entry/<ts>.
    """
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    try:
        limit = int(request.args.get("limit", 500))
    except ValueError:
        limit = 500
    limit = max(1, min(limit, 2000))

    new_timestamps, new_cursor = get_new_timestamps(since_timestamp=since, limit=limit)

    return jsonify({
        'timestamps': new_timestamps,
        'sync_cursor': new_cursor
    })



@app.route("/api/recording-status", methods=["GET"])
def get_recording_status():
    return jsonify({"paused": get_recording_paused()})


@app.route("/api/entry-coords/<int:timestamp>")
def api_entry_coords(timestamp):
    """Return words_coords for a single entry (on-demand loading)."""
    entry = get_entry_by_timestamp(timestamp)
    if not entry:
        return jsonify({'words_coords': [], 'ai_words_coords': []}), 404
    return jsonify({
        'words_coords': entry.words_coords,
        'ai_words_coords': entry.ai_words_coords if entry.ai_words_coords else []
    })


@app.route("/api/pause-recording", methods=["POST"])
def pause_recording():
    set_recording_paused(True)
    return jsonify({"paused": True})


@app.route("/api/resume-recording", methods=["POST"])
def resume_recording():
    set_recording_paused(False)
    return jsonify({"paused": False})


@app.route("/api/viewer-open", methods=["POST"])
def api_viewer_open():
    """The Electron shell reports whether the viewer window is on screen, so the
    capture loop never screenshots the app itself. See screenshot.set_viewer_open."""
    data = request.json or {}
    set_viewer_open(bool(data.get("open", False)))
    return jsonify({"viewer_open": bool(data.get("open", False))})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json
    timestamps = data.get("timestamps", [])
    if not timestamps:
        return jsonify({"error": "No timestamps provided"}), 400
    
    count = delete_entries(timestamps)
    
    # Also delete screenshots from disk
    for ts in timestamps:
        try:
            file_path = os.path.join(screenshots_path, f"{ts}.webp")
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error removing file {ts}.webp: {e}")
            
    return jsonify({"deleted": count})


@app.route("/classic")
def timeline():
    # connect to db
    timestamps = get_timestamps()
    entries = get_entries_metadata()
    entries_dict = {
        entry.timestamp: {
            'id': entry.id,
            'app': entry.app,
            'title': entry.title,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'ai_text': entry.ai_text,
        }
        for entry in entries
    }
    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
{% if timestamps|length > 0 %}
  <div class="toggle-sidebar-btn" onclick="toggleSidebar()" title="Toggle sidebar">
    <i id="sidebarToggleIcon" class="bi bi-chevron-left"></i>
  </div>
  <div class="container-fluid" style="height: calc(100vh - 100px); display: flex; flex-direction: column;">
    <div class="slider-container">
      <input type="range" class="slider custom-range" id="discreteSlider" min="0" max="{{timestamps|length - 1}}" step="1" value="{{timestamps|length - 1}}">
      <div class="slider-value" id="sliderValue">{{timestamps[0] | timestamp_to_human_readable }}</div>
    </div>
    <div class="row flex-grow-1" style="overflow: hidden; margin: 0;">
      <div id="imageColumn" class="col-md-8" style="height: 100%; display: flex; align-items: center; justify-content: center; position: relative; transition: width 0.3s; overscroll-behavior-x: none; padding: 20px;">
        <div id="imageWrapper" style="position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;">
          <img id="timestampImage" src="/static/{{timestamps[0]}}.webp" alt="Image for timestamp" style="max-width: 100%; max-height: 100%; width: auto; height: auto; object-fit: contain; display: block;">
          <div id="textOverlay" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); pointer-events: none;"></div>
        </div>
      </div>
      <div id="sidebarColumn" class="col-md-4 p-3 bg-light border-left" style="height: 100%; overflow-y: auto; display: flex; flex-direction: column; transition: all 0.3s;">
        <div class="mb-3">
          <label class="d-flex align-items-center">
            <input type="checkbox" id="showOverlay" checked class="mr-2">
            <span>Show text blocks on image</span>
          </label>
        </div>
        
        <div class="mb-3">
          <div class="btn-group btn-group-sm w-100" role="group">
            <button type="button" class="btn btn-outline-secondary" id="btnBasicOCR" onclick="switchOCRMode('basic')">
              Basic OCR
            </button>
            <button type="button" class="btn btn-outline-primary" id="btnAIOCR" onclick="switchOCRMode('ai')">
              AI OCR
            </button>
          </div>
          <button class="btn btn-sm btn-success w-100 mt-2" onclick="runAIOCR()" id="btnRunAI">
            <i class="bi bi-robot"></i> Run AI Text
          </button>
          <button class="btn btn-sm btn-secondary w-100 mt-1" onclick="showAIConfig()">
            <i class="bi bi-gear"></i> AI Settings
          </button>
        </div>
        
        <div class="card">
          <div class="card-header d-flex justify-content-between align-items-center" style="cursor: pointer; user-select: none;" onclick="toggleTextPanel()">
            <strong>All Extracted Text</strong>
            <i id="toggleIcon" class="bi bi-chevron-up"></i>
          </div>
          <div id="textPanel" class="card-body" style="max-height: 300px; overflow-y: auto;">
            <div class="d-flex justify-content-end mb-2">
              <button class="btn btn-sm btn-outline-primary" onclick="copyCurrentText()">
                <i class="bi bi-clipboard"></i> Copy All
              </button>
            </div>
            <pre id="extractedText" style="white-space: pre-wrap; word-wrap: break-word; margin: 0; font-size: 0.9em; user-select: text;"></pre>
          </div>
        </div>
      </div>
    </div>
    
    <div class="text-popup-overlay" id="textPopupOverlay" onclick="closeTextPopup()"></div>
    <div class="text-popup" id="textPopup">
      <div class="text-popup-header">
        <strong>Text Block</strong>
        <button type="button" class="close" onclick="closeTextPopup()">&times;</button>
      </div>
      <div class="text-popup-body">
        <pre id="popupText" style="white-space: pre-wrap; word-wrap: break-word; margin: 0; user-select: text;"></pre>
      </div>
      <div class="text-popup-footer">
        <button class="btn btn-sm btn-secondary" onclick="closeTextPopup()">Close</button>
        <button class="btn btn-sm btn-primary" onclick="copyPopupText()">
          <i class="bi bi-clipboard"></i> Copy Text
        </button>
      </div>
    </div>
    
    <!-- AI Config Modal -->
    <div class="text-popup-overlay" id="aiConfigOverlay" onclick="closeAIConfig()"></div>
    <div class="text-popup" id="aiConfigModal">
      <div class="text-popup-header">
        <strong>AI OCR Settings</strong>
        <button type="button" class="close" onclick="closeAIConfig()">&times;</button>
      </div>
      <div class="text-popup-body">
        <div class="form-group">
          <label>AI Provider</label>
          <select class="form-control" id="aiProvider">
            <option value="gemini">Google Gemini</option>
            <option value="openai">OpenAI (GPT-4o)</option>
            <option value="claude">Anthropic Claude</option>
          </select>
        </div>
        <div class="form-group">
          <label>API Key</label>
          <input type="password" class="form-control" id="aiApiKey" placeholder="Enter your API key">
          <small class="form-text text-muted">Your API key is stored locally and never sent to our servers.</small>
        </div>
      </div>
      <div class="text-popup-footer">
        <button class="btn btn-sm btn-secondary" onclick="closeAIConfig()">Cancel</button>
        <button class="btn btn-sm btn-primary" onclick="saveAIConfig()">
          <i class="bi bi-save"></i> Save
        </button>
      </div>
    </div>
  </div>
  <script>
    const timestamps = {{ timestamps|tojson }};
    const entriesData = {{ entries_dict|tojson }};
    const slider = document.getElementById('discreteSlider');
    const sliderValue = document.getElementById('sliderValue');
    const timestampImage = document.getElementById('timestampImage');
    const extractedText = document.getElementById('extractedText');
    const textOverlay = document.getElementById('textOverlay');
    const showOverlayCheckbox = document.getElementById('showOverlay');
    const textPopup = document.getElementById('textPopup');
    const textPopupOverlay = document.getElementById('textPopupOverlay');
    const popupText = document.getElementById('popupText');

    let currentEntry = null;

    async function ensureCoords(entry) {
      if (entry.words_coords !== undefined) return;
      try {
        const resp = await fetch(`/api/entry-coords/${entry.timestamp}`);
        const data = await resp.json();
        entry.words_coords = data.words_coords || [];
        entry.ai_words_coords = data.ai_words_coords || [];
      } catch (e) {
        entry.words_coords = [];
        entry.ai_words_coords = [];
      }
    }

    function groupWordsIntoBlocks(words) {
      if (!words || words.length === 0) return [];
      
      const blocks = [];
      let currentBlock = [words[0]];
      
      for (let i = 1; i < words.length; i++) {
        const prev = words[i - 1];
        const curr = words[i];
        
        // Check if words are on similar Y position (same line) or close vertically
        const verticalDistance = Math.abs(curr.y1 - prev.y1);
        const avgHeight = (curr.y2 - curr.y1 + prev.y2 - prev.y1) / 2;
        
        if (verticalDistance < avgHeight * 0.5) {
          currentBlock.push(curr);
        } else {
          blocks.push(currentBlock);
          currentBlock = [curr];
        }
      }
      blocks.push(currentBlock);
      
      // Merge blocks into text regions
      return blocks.map(block => {
        const minX = Math.min(...block.map(w => w.x1));
        const minY = Math.min(...block.map(w => w.y1));
        const maxX = Math.max(...block.map(w => w.x2));
        const maxY = Math.max(...block.map(w => w.y2));
        const text = block.map(w => w.text).join(' ');
        
        return { x1: minX, y1: minY, x2: maxX, y2: maxY, text };
      });
    }

    async function renderTextOverlay() {
      textOverlay.innerHTML = '';
      if (!showOverlayCheckbox.checked || !currentEntry) return;
      await ensureCoords(currentEntry);
      if (!currentEntry.words_coords || currentEntry.words_coords.length === 0) {
        return;
      }
      
      const img = timestampImage;
      
      // Get actual rendered dimensions of the image
      const displayWidth = img.clientWidth;
      const displayHeight = img.clientHeight;
      
      // Make overlay match image size exactly
      textOverlay.style.width = displayWidth + 'px';
      textOverlay.style.height = displayHeight + 'px';
      
      const blocks = groupWordsIntoBlocks(currentEntry.words_coords);
      
      blocks.forEach((block, index) => {
        const icon = document.createElement('div');
        icon.className = 'text-block-icon';
        icon.innerHTML = '<i class="bi bi-file-text"></i>';
        
        // Center the icon on the block
        const blockWidth = (block.x2 - block.x1) * displayWidth;
        const blockHeight = (block.y2 - block.y1) * displayHeight;
        const iconSize = 32;
        
        const left = block.x1 * displayWidth + blockWidth / 2 - iconSize / 2;
        const top = block.y1 * displayHeight + blockHeight / 2 - iconSize / 2;
        
        icon.style.left = left + 'px';
        icon.style.top = top + 'px';
        icon.title = 'Click to view text';
        icon.onclick = () => showTextPopup(block.text);
        textOverlay.appendChild(icon);
      });
    }

    function showTextPopup(text) {
      popupText.textContent = text;
      textPopup.classList.add('show');
      textPopupOverlay.classList.add('show');
    }

    function closeTextPopup() {
      textPopup.classList.remove('show');
      textPopupOverlay.classList.remove('show');
    }

    function copyPopupText() {
      const text = popupText.textContent;
      navigator.clipboard.writeText(text).then(() => {
        alert('Text copied to clipboard!');
      });
    }

    function updateDisplay(timestamp) {
      sliderValue.textContent = new Date(timestamp / 1000).toLocaleString();
      timestampImage.src = `/static/${timestamp}.webp`;
      currentEntry = entriesData[timestamp];
      extractedText.textContent = currentEntry ? currentEntry.text : 'No text available';
      
      timestampImage.onload = renderTextOverlay;
    }

    slider.addEventListener('input', function() {
      const reversedIndex = timestamps.length - 1 - slider.value;
      const timestamp = timestamps[reversedIndex];
      updateDisplay(timestamp);
    });
    
    showOverlayCheckbox.addEventListener('change', renderTextOverlay);
    window.addEventListener('resize', renderTextOverlay);

    // Video-like scrubbing with trackpad - prevent ALL horizontal scroll from triggering back
    const imageColumn = document.getElementById('imageColumn');
    let accumulatedDelta = 0;
    let isScrolling = false;
    let scrollTimeout = null;
    const sensitivity = 0.25;
    
    // Block back gesture at document level
    document.addEventListener('wheel', function(e) {
      if (Math.abs(e.deltaX) > 0) {
        e.preventDefault();
      }
    }, { passive: false, capture: true });
    
    imageColumn.addEventListener('wheel', function(e) {
      // Only handle horizontal scroll, ignore vertical
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY) && Math.abs(e.deltaX) > 0) {
        e.preventDefault();
        e.stopPropagation();
        
        // Accumulate scroll delta for smooth scrubbing
        accumulatedDelta += e.deltaX * sensitivity;
        
        // Calculate how many frames to move
        const framesToMove = Math.floor(Math.abs(accumulatedDelta));
        
        if (framesToMove >= 1) {
          const direction = accumulatedDelta > 0 ? 1 : -1;
          let newValue = parseInt(slider.value) + (direction * framesToMove);
          
          // Reset accumulated delta
          accumulatedDelta = accumulatedDelta % 1;
          
          // Clamp to valid range
          const oldValue = parseInt(slider.value);
          newValue = Math.max(0, Math.min(timestamps.length - 1, newValue));
          
          // Update even if at boundaries to consume the scroll
          if (newValue !== oldValue) {
            slider.value = newValue;
            const reversedIndex = timestamps.length - 1 - slider.value;
            const timestamp = timestamps[reversedIndex];
            
            // Fast update without overlay during scrubbing
            if (!isScrolling) {
              isScrolling = true;
              showOverlayCheckbox.checked = false;
            }
            
            sliderValue.textContent = new Date(timestamp / 1000).toLocaleString();
            timestampImage.src = `/static/${timestamp}.webp`;
            currentEntry = entriesData[timestamp];
            extractedText.textContent = currentEntry ? currentEntry.text : 'No text available';
          }
          
          // Clear and restart timeout even if we're at boundaries
          clearTimeout(scrollTimeout);
          scrollTimeout = setTimeout(() => {
            isScrolling = false;
            showOverlayCheckbox.checked = true;
            renderTextOverlay();
          }, 300);
        }
      }
    }, { passive: false });
    
    // Arrow keys for precise navigation
    document.addEventListener('keydown', function(e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        const direction = e.key === 'ArrowRight' ? 1 : -1;
        let newValue = parseInt(slider.value) + direction;
        
        newValue = Math.max(0, Math.min(timestamps.length - 1, newValue));
        
        if (newValue !== parseInt(slider.value)) {
          slider.value = newValue;
          const reversedIndex = timestamps.length - 1 - slider.value;
          const timestamp = timestamps[reversedIndex];
          updateDisplay(timestamp);
        }
      }
    });

    function toggleSidebar() {
      const sidebar = document.getElementById('sidebarColumn');
      const imageCol = document.getElementById('imageColumn');
      const icon = document.getElementById('sidebarToggleIcon');
      
      if (sidebar.style.display === 'none') {
        sidebar.style.display = 'flex';
        imageCol.classList.remove('col-md-12');
        imageCol.classList.add('col-md-8');
        icon.className = 'bi bi-chevron-left';
      } else {
        sidebar.style.display = 'none';
        imageCol.classList.remove('col-md-8');
        imageCol.classList.add('col-md-12');
        icon.className = 'bi bi-chevron-right';
      }
      
      // Wait for transition and re-render
      setTimeout(() => {
        renderTextOverlay();
      }, 350);
    }

    function toggleTextPanel() {
      const panel = document.getElementById('textPanel');
      const icon = document.getElementById('toggleIcon');
      
      if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.className = 'bi bi-chevron-up';
      } else {
        panel.style.display = 'none';
        icon.className = 'bi bi-chevron-down';
      }
    }

    function copyCurrentText() {
      const text = extractedText.textContent;
      navigator.clipboard.writeText(text).then(() => {
        alert('Text copied to clipboard!');
      });
    }

    // AI OCR functionality
    let currentOCRMode = 'basic';
    let aiConfig = null;
    
    // Load AI config on startup
    fetch('/api/config')
      .then(r => r.json())
      .then(config => {
        aiConfig = config;
      });
    
    function switchOCRMode(mode) {
      currentOCRMode = mode;
      document.getElementById('btnBasicOCR').classList.toggle('btn-secondary', mode !== 'basic');
      document.getElementById('btnBasicOCR').classList.toggle('btn-primary', mode === 'basic');
      document.getElementById('btnAIOCR').classList.toggle('btn-secondary', mode !== 'ai');
      document.getElementById('btnAIOCR').classList.toggle('btn-primary', mode === 'ai');
      
      if (currentEntry) {
        ensureCoords(currentEntry).then(() => {
          if (mode === 'ai' && currentEntry.ai_text) {
            extractedText.textContent = currentEntry.ai_text;
            currentEntry.words_coords = (currentEntry.ai_words_coords && currentEntry.ai_words_coords.length > 0)
              ? currentEntry.ai_words_coords
              : currentEntry.words_coords;
          } else {
            extractedText.textContent = currentEntry.text;
          }
          renderTextOverlay();
        });
      }
    }
    
    async function runAIOCR() {
      if (!currentEntry) {
        alert('No screenshot selected');
        return;
      }
      
      const btn = document.getElementById('btnRunAI');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm mr-1"></span> Processing...';
      
      try {
        // Load real API key from backend
        const configResp = await fetch('/api/config?full=true');
        const fullConfig = await configResp.json();
        
        if (!fullConfig.api_key || fullConfig.api_key === '***' || fullConfig.api_key === '') {
          alert('Please configure AI settings first');
          showAIConfig();
          btn.disabled = false;
          btn.innerHTML = '<i class="bi bi-robot"></i> Run AI Text';
          return;
        }
        
        const response = await fetch('/api/ai-ocr', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            timestamp: currentEntry.timestamp,
            provider: fullConfig.provider || 'gemini',
            api_key: fullConfig.api_key
          })
        });
        
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error || 'AI OCR failed');
        }
        
        const result = await response.json();
        
        // Update current entry
        currentEntry.ai_text = result.text;
        currentEntry.ai_words_coords = result.words_coords;
        entriesData[currentEntry.timestamp].ai_text = result.text;
        entriesData[currentEntry.timestamp].ai_words_coords = result.words_coords;
        
        // Switch to AI mode
        switchOCRMode('ai');
        
        alert('AI OCR completed successfully!');
      } catch (error) {
        alert('AI OCR error: ' + error.message);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-robot"></i> Run AI Text';
      }
    }
    
    function showAIConfig() {
      const modal = document.getElementById('aiConfigModal');
      const overlay = document.getElementById('aiConfigOverlay');
      
      if (aiConfig) {
        document.getElementById('aiProvider').value = aiConfig.provider || 'gemini';
        // Don't show the masked key
        document.getElementById('aiApiKey').value = '';
        document.getElementById('aiApiKey').placeholder = aiConfig.api_key === '***' ? 'Enter new API key' : 'Enter your API key';
      }
      
      modal.classList.add('show');
      overlay.classList.add('show');
    }
    
    function closeAIConfig() {
      const modal = document.getElementById('aiConfigModal');
      const overlay = document.getElementById('aiConfigOverlay');
      modal.classList.remove('show');
      overlay.classList.remove('show');
    }
    
    async function saveAIConfig() {
      const provider = document.getElementById('aiProvider').value;
      const apiKey = document.getElementById('aiApiKey').value;
      
      if (!apiKey) {
        alert('Please enter an API key');
        return;
      }
      
      try {
        const response = await fetch('/api/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({provider, api_key: apiKey})
        });
        
        if (response.ok) {
          aiConfig = {provider, api_key: '***'};
          alert('AI settings saved successfully!');
          closeAIConfig();
        } else {
          alert('Failed to save settings');
        }
      } catch (error) {
        alert('Error saving settings: ' + error.message);
      }
    }

    // Initialize the slider with a default value
    slider.value = timestamps.length - 1;
    updateDisplay(timestamps[0]);
  </script>
{% else %}
  <div class="container">
      <div class="alert alert-info" role="alert">
          Nothing recorded yet, wait a few seconds.
      </div>
  </div>
{% endif %}
{% endblock %}
""",
        timestamps=timestamps,
        entries_dict=entries_dict,
    )


@app.route("/search")
def search():
    q = request.args.get("q")
    if not q or not q.strip():
        return render_template_string(
            """
{% extends "base_template" %}
{% block content %}
    <div class="container mt-4">
        <div class="alert alert-info">Please enter a search query</div>
    </div>
{% endblock %}
""")
    
    query_embedding = get_embedding(q)
    search_results = search_entries_streaming(query_embedding, query_text=q, limit=50)

    # Enrich results with words_coords for the classic search view
    sorted_entries = []
    for r in search_results["results"]:
        entry = get_entry_by_timestamp(r['timestamp'])
        sorted_entries.append({
            'id': r['id'],
            'app': r['app'],
            'title': r['title'],
            'text': r['text'],
            'timestamp': r['timestamp'],
            'words_coords': entry.words_coords if entry else [],
            'ai_text': entry.ai_text if entry else None,
            'ai_words_coords': entry.ai_words_coords if entry and entry.ai_words_coords else []
        })

    return render_template_string(
        """
{% extends "base_template" %}
{% block content %}
    <div class="container">
        <div class="row">
            {% for entry in entries %}
                <div class="col-md-3 mb-4">
                    <div class="card">
                        <a href="#" data-toggle="modal" data-target="#modal-{{ loop.index0 }}">
                            <img src="/static/{{ entry['timestamp'] }}.webp" alt="Image" class="card-img-top">
                        </a>
                    </div>
                </div>
                <div class="modal fade" id="modal-{{ loop.index0 }}" tabindex="-1" role="dialog" aria-labelledby="exampleModalLabel" aria-hidden="true">
                    <div class="modal-dialog modal-xl" role="document" style="max-width: none; width: 100vw; height: 100vh; padding: 20px;">
                        <div class="modal-content" style="height: calc(100vh - 40px); width: calc(100vw - 40px); padding: 0;">
                            <div class="modal-body" style="padding: 0; display: flex; height: 100%;">
                                <div style="flex: 2; display: flex; align-items: center; justify-content: center; overflow: auto; padding: 10px; position: relative;">
                                    <div style="position: relative; display: inline-block;">
                                        <img id="modalImg{{ loop.index0 }}" src="/static/{{ entry['timestamp'] }}.webp" alt="Image" style="max-width: 100%; max-height: 100%; object-fit: contain; display: block;">
                                        <div id="modalOverlay{{ loop.index0 }}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none;"></div>
                                    </div>
                                    
                                    <div style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 2000; display: none;" id="modalPopupOverlay{{ loop.index0 }}" onclick="closeModalTextPopup{{ loop.index0 }}()"></div>
                                    <div style="position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: white; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); max-width: 600px; max-height: 80vh; overflow: hidden; z-index: 2001; display: none;" id="modalTextPopup{{ loop.index0 }}">
                                        <div style="padding: 15px; border-bottom: 1px solid #dee2e6; display: flex; justify-content: space-between; align-items: center;">
                                            <strong>Text Block</strong>
                                            <button type="button" class="close" onclick="closeModalTextPopup{{ loop.index0 }}()">&times;</button>
                                        </div>
                                        <div style="padding: 15px; max-height: 60vh; overflow-y: auto;">
                                            <pre id="modalPopupText{{ loop.index0 }}" style="white-space: pre-wrap; word-wrap: break-word; margin: 0; user-select: text;"></pre>
                                        </div>
                                        <div style="padding: 15px; border-top: 1px solid #dee2e6; display: flex; justify-content: flex-end; gap: 10px;">
                                            <button class="btn btn-sm btn-secondary" onclick="closeModalTextPopup{{ loop.index0 }}()">Close</button>
                                            <button class="btn btn-sm btn-primary" onclick="copyModalPopupText{{ loop.index0 }}()">
                                                <i class="bi bi-clipboard"></i> Copy Text
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                {% if entry['text'] %}
                                <div class="p-3 bg-light border-left" style="flex: 1; overflow-y: auto; min-width: 300px;">
                                    <div class="mb-3">
                                        <label class="d-flex align-items-center">
                                            <input type="checkbox" id="showModalOverlay{{ loop.index0 }}" checked class="mr-2">
                                            <span>Show text overlay</span>
                                        </label>
                                    </div>
                                    <div class="d-flex justify-content-between align-items-center mb-2">
                                        <strong>Extracted Text:</strong>
                                        <button class="btn btn-sm btn-outline-primary" onclick="copyText{{ loop.index0 }}()">
                                            <i class="bi bi-clipboard"></i> Copy
                                        </button>
                                    </div>
                                    <pre id="text-{{ loop.index0 }}" style="white-space: pre-wrap; word-wrap: break-word; margin: 0; font-size: 0.9em; user-select: text;">{{ entry['text'] }}</pre>
                                </div>
                                <script>
                                    (function() {
                                        const wordsCoords = {{ entry['words_coords']|tojson }};
                                        const img = document.getElementById('modalImg{{ loop.index0 }}');
                                        const overlay = document.getElementById('modalOverlay{{ loop.index0 }}');
                                        const checkbox = document.getElementById('showModalOverlay{{ loop.index0 }}');
                                        
                                        function groupWordsIntoBlocks(words) {
                                            if (!words || words.length === 0) return [];
                                            
                                            const blocks = [];
                                            let currentBlock = [words[0]];
                                            
                                            for (let i = 1; i < words.length; i++) {
                                                const prev = words[i - 1];
                                                const curr = words[i];
                                                
                                                const verticalDistance = Math.abs(curr.y1 - prev.y1);
                                                const avgHeight = (curr.y2 - curr.y1 + prev.y2 - prev.y1) / 2;
                                                
                                                if (verticalDistance < avgHeight * 0.5) {
                                                    currentBlock.push(curr);
                                                } else {
                                                    blocks.push(currentBlock);
                                                    currentBlock = [curr];
                                                }
                                            }
                                            blocks.push(currentBlock);
                                            
                                            return blocks.map(block => {
                                                const minX = Math.min(...block.map(w => w.x1));
                                                const minY = Math.min(...block.map(w => w.y1));
                                                const maxX = Math.max(...block.map(w => w.x2));
                                                const maxY = Math.max(...block.map(w => w.y2));
                                                const text = block.map(w => w.text).join(' ');
                                                
                                                return { x1: minX, y1: minY, x2: maxX, y2: maxY, text };
                                            });
                                        }
                                        
                                        function showModalTextPopup{{ loop.index0 }}(text) {
                                            const existingPopup = document.getElementById('modalTextPopup{{ loop.index0 }}');
                                            if (existingPopup) {
                                                document.getElementById('modalPopupText{{ loop.index0 }}').textContent = text;
                                                existingPopup.style.display = 'block';
                                                document.getElementById('modalPopupOverlay{{ loop.index0 }}').style.display = 'block';
                                            }
                                        }
                                        
                                        function closeModalTextPopup{{ loop.index0 }}() {
                                            document.getElementById('modalTextPopup{{ loop.index0 }}').style.display = 'none';
                                            document.getElementById('modalPopupOverlay{{ loop.index0 }}').style.display = 'none';
                                        }
                                        
                                        window.closeModalTextPopup{{ loop.index0 }} = closeModalTextPopup{{ loop.index0 }};
                                        
                                        function copyModalPopupText{{ loop.index0 }}() {
                                            const text = document.getElementById('modalPopupText{{ loop.index0 }}').textContent;
                                            navigator.clipboard.writeText(text).then(() => {
                                                alert('Text copied to clipboard!');
                                            });
                                        }
                                        
                                        window.copyModalPopupText{{ loop.index0 }} = copyModalPopupText{{ loop.index0 }};
                                        
                                        function renderModalOverlay() {
                                            overlay.innerHTML = '';
                                            if (!checkbox.checked || !wordsCoords) return;
                                            
                                            const displayWidth = img.width;
                                            const displayHeight = img.height;
                                            
                                            const blocks = groupWordsIntoBlocks(wordsCoords);
                                            
                                            blocks.forEach((block, index) => {
                                                const icon = document.createElement('div');
                                                icon.className = 'text-block-icon';
                                                icon.innerHTML = '<i class="bi bi-file-text"></i>';
                                                
                                                // Center the icon on the block
                                                const blockWidth = (block.x2 - block.x1) * displayWidth;
                                                const blockHeight = (block.y2 - block.y1) * displayHeight;
                                                const iconSize = 32;
                                                
                                                icon.style.left = (block.x1 * displayWidth + blockWidth / 2 - iconSize / 2) + 'px';
                                                icon.style.top = (block.y1 * displayHeight + blockHeight / 2 - iconSize / 2) + 'px';
                                                icon.title = 'Click to view text';
                                                icon.onclick = () => showModalTextPopup{{ loop.index0 }}(block.text);
                                                overlay.appendChild(icon);
                                            });
                                        }
                                        
                                        img.onload = renderModalOverlay;
                                        checkbox.addEventListener('change', renderModalOverlay);
                                        document.getElementById('modal-{{ loop.index0 }}').addEventListener('shown.bs.modal', renderModalOverlay);
                                    })();
                                    
                                    function copyText{{ loop.index0 }}() {
                                        const text = document.getElementById('text-{{ loop.index0 }}').textContent;
                                        navigator.clipboard.writeText(text).then(() => {
                                            alert('Text copied to clipboard!');
                                        });
                                    }
                                </script>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>
            {% endfor %}
        </div>
    </div>
{% endblock %}
""",
        entries=sorted_entries,
    )


@app.route("/static/<filename>")
def serve_image(filename):
    return send_from_directory(screenshots_path, filename)


@app.route("/api/ai-ocr", methods=["POST"])
def ai_ocr():
    """Endpoint to perform AI OCR on a screenshot"""
    try:
        data = request.json
        timestamp = data.get('timestamp')
        provider = data.get('provider', 'gemini')
        api_key = data.get('api_key')
        
        if not timestamp or not api_key:
            return jsonify({'error': 'Missing timestamp or api_key'}), 400
        
        # Find the entry
        entry = get_entry_by_timestamp(timestamp)
        
        if not entry:
            return jsonify({'error': 'Entry not found'}), 404
        
        # Load the screenshot image
        image_path = os.path.join(screenshots_path, f"{timestamp}.webp")
        if not os.path.exists(image_path):
            return jsonify({'error': 'Screenshot file not found'}), 404
        
        # Convert image to base64
        with Image.open(image_path) as img:
            # Convert to RGB if necessary
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Save to bytes
            import io
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=95)
            image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        
        # Get AI provider
        ai_provider = get_ai_provider(provider, api_key)
        
        # Perform AI OCR
        ai_text, ai_words_coords = ai_provider.ocr_with_positions(
            image_base64,
            entry.text
        )
        
        # Update database
        update_ai_ocr(timestamp, ai_text, ai_words_coords)
        
        return jsonify({
            'success': True,
            'text': ai_text,
            'words_coords': ai_words_coords
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def ai_config():
    """Endpoint to manage AI OCR configuration"""
    config_path = os.path.join(appdata_folder, "ai_config.json")
    
    if request.method == "GET":
        full = request.args.get('full') == 'true'
        
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                import json
                config = json.load(f)
                # Mask API key unless full=true
                if not full:
                    config['api_key'] = '***' if config.get('api_key') else ''
                return jsonify(config)
        return jsonify({'provider': 'gemini', 'api_key': ''})
    
    elif request.method == "POST":
        data = request.json
        import json
        with open(config_path, 'w') as f:
            json.dump(data, f)
        return jsonify({'success': True})


@app.route("/api/settings/retention", methods=["GET", "POST"])
def api_settings_retention():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    if request.method == "GET":
        days = -1
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
                        days = settings.get('retention_days', -1)
            except Exception:
                pass
        return jsonify({'days': str(days)})
    else:
        data = request.json
        days = data.get('days', -1)
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            except Exception:
                pass
        settings['retention_days'] = int(days)
        with open(settings_path, 'w') as f:
            json.dump(settings, f)
        return jsonify({'success': True})


@app.route("/api/settings/interval", methods=["GET", "POST"])
def api_settings_interval():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    if request.method == "GET":
        interval = get_screenshot_interval()
        return jsonify({'interval': str(interval)})
    else:
        data = request.json
        interval = int(data.get('interval', 3))
        set_screenshot_interval(interval)
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            except Exception:
                pass
        settings['screenshot_interval'] = interval
        with open(settings_path, 'w') as f:
            json.dump(settings, f)
        return jsonify({'success': True})


@app.route("/api/settings/ocr-cooldown", methods=["GET", "POST"])
def api_settings_ocr_cooldown():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    if request.method == "GET":
        return jsonify({'ocr_cooldown': str(get_ocr_cooldown())})
    else:
        data = request.json
        cooldown = int(data.get('ocr_cooldown', 90))
        set_ocr_cooldown(cooldown)
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            except Exception:
                pass
        settings['ocr_cooldown'] = cooldown
        with open(settings_path, 'w') as f:
            json.dump(settings, f)
        return jsonify({'success': True})


@app.route("/api/settings/ocr-compute-mode", methods=["GET", "POST"])
def api_settings_ocr_compute_mode():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    if request.method == "GET":
        return jsonify({'ocr_compute_mode': get_ocr_compute_mode()})
    else:
        data = request.json
        mode = data.get('ocr_compute_mode', 'smart')
        set_ocr_compute_mode(mode)
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            except Exception:
                pass
        settings['ocr_compute_mode'] = mode
        with open(settings_path, 'w') as f:
            json.dump(settings, f)
        return jsonify({'success': True})


@app.route("/api/settings/apple_vision", methods=["GET"])
def api_get_apple_vision_setting():
    return jsonify({
        "enabled": get_use_apple_vision(),
        "available": is_apple_vision_available(),
    })


@app.route("/api/settings/apple_vision", methods=["POST"])
def api_set_apple_vision_setting():
    import json
    data = request.get_json(force=True, silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    set_use_apple_vision(enabled)
    settings_path = os.path.join(appdata_folder, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                content = f.read().strip()
                if content:
                    settings = json.loads(content)
        except Exception:
            settings = {}
    settings["use_apple_vision"] = enabled
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=4)
    return jsonify({"enabled": enabled, "available": is_apple_vision_available()})


@app.route("/api/settings/quality", methods=["GET", "POST"])
def api_settings_quality():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    
    if request.method == "POST":
        data = request.json
        quality = data.get("quality", "medium")
        if quality not in ['low', 'medium', 'high']:
            return jsonify({'success': False, 'error': 'Invalid quality'}), 400
            
        set_screenshot_quality(quality)
        
        # Save to file
        try:
            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            
            settings['screenshot_quality'] = quality
            
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=4)
                
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
            
    else:
        return jsonify({'quality': get_screenshot_quality()})


@app.route("/api/settings/skip_incognito", methods=["GET", "POST"])
def api_settings_skip_incognito():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json

    if request.method == "POST":
        data = request.json
        skip = data.get("skip_incognito", True)
        set_skip_incognito_recording(bool(skip))

        # Save to file
        try:
            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)

            settings['skip_incognito'] = bool(skip)

            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=4)

            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    else:
        return jsonify({'skip_incognito': get_skip_incognito_recording()})


@app.route("/api/settings/port", methods=["GET", "POST"])
def api_settings_port():
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    
    if request.method == "POST":
        data = request.json
        try:
            port = int(data.get("port", 8082))
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid port'}), 400
            
        # Save to file
        try:
            settings = {}
            if os.path.exists(settings_path):
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
            
            settings['server_port'] = port
            
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=4)
                
            return jsonify({'success': True, 'message': 'Restart required'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
            
    else:
        # Read from file as it's not a dynamic runtime setting
        port = 8082
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        settings = json.loads(content)
                        port = settings.get('server_port', 8082)
            except:
                pass
        return jsonify({'port': port})


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    """Unified endpoint to update all settings atomically"""
    settings_path = os.path.join(appdata_folder, "settings.json")
    import json
    
    data = request.json
    restart_required = False
    
    # Load current settings
    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                content = f.read().strip()
                if content:
                    settings = json.loads(content)
        except Exception:
            pass # Start fresh if corrupt
    
    # Update Interval
    if 'interval' in data:
        interval = int(data['interval'])
        set_screenshot_interval(interval)
        settings['screenshot_interval'] = interval
        
    # Update Retention
    if 'retention_days' in data:
        settings['retention_days'] = int(data['retention_days'])
        
    # Update Quality
    if 'quality' in data:
        quality = data['quality']
        if quality in ['low', 'medium', 'high']:
            set_screenshot_quality(quality)
            settings['screenshot_quality'] = quality

    # Update OCR Cooldown
    if 'ocr_cooldown' in data:
        cooldown = int(data['ocr_cooldown'])
        set_ocr_cooldown(cooldown)
        settings['ocr_cooldown'] = cooldown

    # Update OCR Compute Mode
    if 'ocr_compute_mode' in data:
        mode = data['ocr_compute_mode']
        set_ocr_compute_mode(mode)
        settings['ocr_compute_mode'] = mode

    # Update Apple Vision OCR engine
    if 'use_apple_vision' in data:
        set_use_apple_vision(bool(data['use_apple_vision']))
        settings['use_apple_vision'] = bool(data['use_apple_vision'])

    # Update Skip Incognito
    if 'skip_incognito' in data:
        skip = bool(data['skip_incognito'])
        set_skip_incognito_recording(skip)
        settings['skip_incognito'] = skip

    # Update Port
    if 'port' in data:
        try:
            new_port = int(data['port'])
            old_port = settings.get('server_port', 8082)
            if new_port != old_port:
                settings['server_port'] = new_port
                restart_required = True
        except ValueError:
            pass

    # Save atomically
    try:
        with open(settings_path, 'w') as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    return jsonify({'success': True, 'restart_required': restart_required})


if __name__ == "__main__":
    import socket
    import sys
    import json
    
    # 1. Load settings to get configured port
    configured_port = 8082
    settings_path = os.path.join(appdata_folder, "settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r') as f:
                content = f.read().strip()
                if content:
                    settings = json.loads(content)
                    configured_port = int(settings.get('server_port', 8082))
        except Exception as e:
            print(f"Error reading port config: {e}")

    # 2. Check if port is already in use
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_in_use = sock.connect_ex(('127.0.0.1', configured_port)) == 0
    sock.close()
    
    if port_in_use:
        print(f"❌ Port {configured_port} is already in use. OpenReLife is already running.")
        print("💡 Use the hotkey (Cmd+Shift+Space) to open the interface.")
        sys.exit(1)
    
    create_db()
    load_settings()

    print(f"Appdata folder: {appdata_folder}")
    print(f"🚀 Starting OpenReLife on port {configured_port} (Production Mode)...")

    # Warm the in-memory embedding index in the background (issue #11): the first
    # ~12s load happens off the request path; until it's ready, search falls back to
    # the DB scan, so serve() is never blocked and behaviour is never worse.
    from openrelife import embedding_index
    embedding_index.start()

    # Start capture thread (fast, every 3s)
    t = Thread(target=record_screenshots_thread, daemon=True)
    t.start()

    # Start OCR worker thread (processes queue in background)
    ocr_t = Thread(target=ocr_worker_thread, daemon=True)
    ocr_t.start()

    # Use Waitress for production. 16 threads gives headroom so static file
    # serving (/static/*.webp for the timeline scrubber) doesn't queue behind
    # slower DB-bound endpoints (/api/sync, /api/entry/<ts>) during OCR bursts.
    from waitress import serve
    serve(app, host='127.0.0.1', port=configured_port, threads=16)
