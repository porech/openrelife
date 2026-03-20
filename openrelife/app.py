from threading import Thread
import os
import base64

import numpy as np
from flask import Flask, render_template_string, request, send_from_directory, jsonify
from jinja2 import BaseLoader
from PIL import Image

from openrelife.config import appdata_folder, screenshots_path
from openrelife.database import create_db, get_all_entries, get_timestamps, update_ai_ocr, delete_entries, get_entry_by_timestamp
from openrelife.nlp import cosine_similarity, get_embedding
from openrelife.screenshot import (
    record_screenshots_thread,
    get_recording_paused,
    set_recording_paused,
    get_screenshot_interval,
    set_screenshot_interval,
    get_screenshot_quality,
    set_screenshot_quality,
    get_skip_incognito_recording,
    set_skip_incognito_recording,
)
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
        entries = get_all_entries(limit=limit)
    else:
        partial_timestamps = all_timestamps
        entries = get_all_entries()

    entries_dict = {
        entry.timestamp: {
            'id': entry.id,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'words_coords': entry.words_coords,
            'ai_text': entry.ai_text,
            'ai_words_coords': entry.ai_words_coords if entry.ai_words_coords else []
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
    .search-container { position: fixed; top: 20px; right: 20px; z-index: 1000; }
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
    .search-spinner {
      position: absolute; right: 15px; top: 50%; transform: translateY(-50%);
      color: rgba(0,123,255,0.8); animation: spin 1s linear infinite;
    }
    @keyframes spin { from { transform: translateY(-50%) rotate(0deg); } to { transform: translateY(-50%) rotate(360deg); } }
    
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
    .result-card img { width: 100%; height: 120px; object-fit: cover; }
    .result-time { padding: 8px 12px; font-size: 11px; color: rgba(255,255,255,0.6); text-align: center; }
    
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
      background: #1e1e1e; width: 500px; max-width: 90%;
      border-radius: 12px; border: 1px solid rgba(255, 255, 255, 0.1);
      box-shadow: 0 20px 60px rgba(0,0,0,0.5); transform: translateY(20px);
      transition: transform 0.3s ease; display: flex; flex-direction: column;
    }
    .settings-modal-overlay.show .settings-modal { transform: translateY(0); }
    
    .settings-modal-header {
      padding: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      display: flex; justify-content: space-between; align-items: center;
    }
    .settings-modal-header h2 { font-size: 20px; font-weight: 600; margin: 0; }
    
    .settings-modal-body { padding: 20px; }
    
    .settings-modal-footer {
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
    .form-group select, .form-group input {
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
    <!-- Sidebar toggle -->
    <div class="sidebar-toggle" onclick="toggleSidebar()">
      <i class="bi bi-list" style="font-size: 24px;"></i>
    </div>
    
    <!-- Sidebar -->
    <div class="sidebar" id="sidebar">
      <button class="sidebar-close" onclick="toggleSidebar()">&times;</button>
      
      <div class="sidebar-section">
        <h3>App</h3>
        <button class="sidebar-btn" onclick="hideAppWindow()">
          <i class="bi bi-window-dash"></i> Nascondi finestra
        </button>
        <button class="sidebar-btn" onclick="quitAppFromMenu()">
          <i class="bi bi-power"></i> Chiudi OpenReLife
        </button>
      </div>

      <div class="sidebar-section">
        <h3>OCR Settings</h3>
        
        <div class="ocr-mode-selector">
          <span class="ocr-mode-label">Basic OCR</span>
          <label class="toggle-switch">
            <input type="checkbox" id="ocrToggle" onchange="toggleOCRMode()">
            <span class="toggle-slider"></span>
          </label>
          <span class="ocr-mode-label">AI OCR</span>
        </div>
        
        <button class="sidebar-btn primary" onclick="runAIOCR()" id="btnRunAI">
          <i class="bi bi-stars"></i> Run AI Text
        </button>
        <button class="sidebar-btn" onclick="showAIConfig()">
          <i class="bi bi-gear"></i> Configure AI Provider
        </button>
      </div>
      
      <div class="sidebar-section">
        <h3>Extracted Text</h3>
        <button class="sidebar-btn" onclick="copyExtractedText()" style="margin-bottom: 12px;">
          <i class="bi bi-clipboard"></i> Copy All
        </button>
        <pre id="extractedText"></pre>
      </div>

    </div>
    
    <!-- Search bar -->
    <div class="search-container">
      <div class="search-wrapper">
        <input type="text" class="search-input" id="searchInput" placeholder="Search your history...">
        <i class="bi bi-search search-icon" id="searchIcon"></i>
        <i class="bi bi-arrow-clockwise search-spinner" id="searchSpinner" style="display: none;"></i>
        <i class="bi bi-x-circle-fill clear-icon" id="searchClear" style="display: none;"></i>
      </div>
    </div>
    
    <!-- Search results -->
    <div class="search-results" id="searchResults"></div>
    
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
               <i class="bi bi-trash"></i> Cancella
             </div>
          </div>
        </div>
        
        <input type="range" class="timeline-slider" id="timelineSlider" 
               min="0" max="{{timestamps|length - 1}}" value="{{timestamps|length - 1}}">
               
        <div class="delete-controls" id="deleteControls" style="display: none;">
           <button class="btn-delete-confirm" id="btnConfirmDelete" onclick="confirmDelete()">
             <i class="bi bi-trash-fill"></i> Elimina selezione
           </button>
           <div class="delete-info" id="deleteInfo">1 screenshot</div>
           <button class="btn-delete-cancel" onclick="exitDeleteMode()">Annulla</button>
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
        
        <div class="form-group" style="margin-top: 24px;">
          <label>
            Screenshot Interval (seconds)
            <div class="tooltip-container">
              <i class="bi bi-question-circle" style="cursor: help; margin-left: 4px; color: rgba(255,255,255,0.4);"></i>
              <span class="tooltip-text">The effective time between captures will be this interval <strong>PLUS</strong> the OCR processing time (usually 10-20s).</span>
            </div>
          </label>
          <input type="text" inputmode="numeric" id="intervalInput" class="form-control" oninput="this.value = this.value.replace(/[^0-9]/g, ''); checkIntervalWarning(this.value)" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; height: auto; padding: 0.375rem 0.75rem;">
          <div id="intervalWarning" style="margin-top: 12px; color: #ffc107; display: none; background: rgba(255, 193, 7, 0.1); padding: 12px; border-radius: 8px; border-left: 4px solid #ffc107; font-size: 13px;">
            <i class="bi bi-exclamation-triangle-fill" style="margin-right: 8px;"></i>
            <strong>Warning:</strong> setting a value below 3 seconds will cause an <strong>unproportional</strong> increase in CPU and disk usage. Proceed only if strictly necessary.
          </div>
        </div>

        <div class="form-group" style="margin-top: 24px;">
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

        <div class="form-group" style="margin-top: 24px;">
          <label style="display: flex; align-items: center; cursor: pointer;">
            <input type="checkbox" id="skipIncognitoCheckbox" checked style="width: 18px; height: 18px; margin-right: 10px; accent-color: #0d6efd;">
            Skip recording in incognito/private mode
          </label>
          <small class="form-text text-muted" style="margin-top: 8px;">
            When enabled, screenshots will not be captured while a browser is in incognito or private browsing mode.
          </small>
        </div>

        <div class="form-group" style="margin-top: 24px;">
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

      if (!confirm('Chiudere OpenReLife?')) return;
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

    // Smart Sync Logic
    let syncInterval = null;

    async function syncData() {
      if (timestamps.length === 0) return;
      const lastKnown = timestamps[0];
      try {
        // Smart Resume: Check if we are currently at the latest timestamp BEFORE syncing
        const wasAtLatest = parseInt(slider.value) === parseInt(slider.max);

        const response = await fetch(`/api/sync?since=${lastKnown}`);
        const data = await response.json();
        if (data.timestamps && data.timestamps.length > 0) {
          timestamps = [...data.timestamps, ...timestamps];
          entriesData = {...entriesData, ...data.entries};
          slider.max = timestamps.length - 1;
          console.log(`Synced ${data.timestamps.length} new entries.`);
          
          // If we were at the latest, stay at the latest (which is now a new timestamp)
          if (wasAtLatest) {
             slider.value = slider.max;
             updateDisplay(timestamps[0]); // timestamps[0] is the new latest
          }
          
          updateJumpButtonVisibility();
        }
      } catch (e) { console.error("Sync failed", e); }
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
    
    // Update display
    // Update display
    async function updateDisplay(timestamp) {
      // Update basic UI immediately
      screenshot.src = `/static/${timestamp}.webp`;
      dateEl.textContent = new Date(timestamp / 1000).toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit', hour12: true
      });
      
      // Check if we have data
      if (entriesData[timestamp]) {
          currentEntry = entriesData[timestamp];
          
          // If image already loaded, render immediately, otherwise wait for onload
          if (screenshot.complete && screenshot.naturalHeight !== 0) {
              renderOverlay();
          }
          screenshot.onload = renderOverlay;
          
          updateExtractedText();
      } else {
          // Loading state
          currentEntry = null;
          renderOverlay(); // Clears overlay
          document.getElementById('extractedText').innerHTML = '<span class="text-muted"><i class="bi bi-arrow-clockwise spinner-border spinner-border-sm"></i> Loading info...</span>';
          
          try {
              if (currentAbortController) currentAbortController.abort();
              currentAbortController = new AbortController();
              
              const res = await fetch(`/api/entry/${timestamp}`, { signal: currentAbortController.signal });
              const data = await res.json();
              
              if (data.success) {
                  entriesData[timestamp] = data;
                  
                  // Check if user is still on this timestamp
                  const sliderVal = parseInt(slider.value);
                  const currentIdx = timestamps.length - 1 - sliderVal;
                  if (timestamps[currentIdx] === timestamp) {
                      currentEntry = data;
                      if (screenshot.complete) renderOverlay();
                      updateExtractedText();
                  }
              } else {
                  document.getElementById('extractedText').textContent = "Info not available.";
              }
          } catch (e) {
              if (e.name === 'AbortError') return;
              console.error("Fetch error", e);
              document.getElementById('extractedText').textContent = "Error loading info.";
          }
      }
      
      // Trigger prefetch for neighbors with debounce
      // This prevents thousands of requests when scrolling quickly
      clearTimeout(prefetchTimeout);
      prefetchTimeout = setTimeout(() => {
          const sliderVal = parseInt(slider.value);
          const currentIdx = timestamps.length - 1 - sliderVal;
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
         if (window.electronAPI) {
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
      } else if (e.key === 'Escape') {
        closeTextPopup();
        searchResults.classList.remove('show');
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
    
    function renderOverlay() {
      textOverlay.innerHTML = '';
      if (!currentEntry || !currentEntry.words_coords) return;
      
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
    const searchSpinner = document.getElementById('searchSpinner');

    // Search
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      const q = searchInput.value.trim();
      
      // Toggle icons
      if (searchInput.value.length > 0) {
        searchIcon.style.display = 'none';
        searchClear.style.display = 'block';
      } else {
        searchIcon.style.display = 'block';
        searchClear.style.display = 'none';
      }
      
      if (!q) {
        searchResults.classList.remove('show');
        return;
      }
      
      searchTimeout = setTimeout(() => performSearch(q), 600);
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchInput.dispatchEvent(new Event('input'));
      searchInput.focus();
    });
    
    async function performSearch(q) {
      if (searchController) searchController.abort();
      searchController = new AbortController();

      // Show loading spinner
      searchIcon.style.display = 'none';
      searchSpinner.style.display = 'block';

      try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: searchController.signal });
        const results = await response.json();

        if (results.length === 0) {
          searchResults.innerHTML = '<p style="color: rgba(255,255,255,0.5); text-align: center;">No results found</p>';
        } else {
          searchResults.innerHTML = '<div class="results-grid">' +
            results.map(r => `
              <div class="result-card" onclick="goToTimestamp(${r.timestamp})">
                <img src="/static/${r.timestamp}.webp" alt="">
                <div class="result-time">${new Date(r.timestamp/1000).toLocaleString('en-US', {
                  month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
                })}</div>
              </div>
            `).join('') + '</div>';
        }
        searchResults.classList.add('show');
      } catch (err) {
        if (err.name === 'AbortError') return;
        console.error('Search error:', err);
      } finally {
        // Hide loading spinner
        searchSpinner.style.display = 'none';
      }
    }
    
    function goToTimestamp(ts) {
      const idx = timestamps.indexOf(ts);
      if (idx !== -1) {
        slider.value = timestamps.length - 1 - idx;
        updateDisplay(ts);
        searchInput.value = '';
        searchInput.dispatchEvent(new Event('input'));
      }
    }
    
    // Sidebar
    function toggleSidebar() {
      document.getElementById('sidebar').classList.toggle('open');
    }
    
    let currentOCRMode = 'basic';
    let aiConfig = null;
    
    fetch('/api/config').then(r => r.json()).then(c => aiConfig = c);
    
    function toggleOCRMode() {
      const toggle = document.getElementById('ocrToggle');
      currentOCRMode = toggle.checked ? 'ai' : 'basic';
      updateExtractedText();
    }
    
    function showOCRMode(mode) {
      currentOCRMode = mode;
      document.getElementById('ocrToggle').checked = (mode === 'ai');
      updateExtractedText();
    }
    
    async function runAIOCR() {
      if (!currentEntry) {
        showToast('No screenshot selected', 'error');
        return;
      }
      
      const btn = document.getElementById('btnRunAI');
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Processing...';
      
      // Show info toast about processing time
      showToast('AI OCR is processing... This may take 10-30 seconds as the AI analyzes the entire screenshot.', 'info');
      
      try {
        const configResp = await fetch('/api/config?full=true');
        const fullConfig = await configResp.json();
        
        if (!fullConfig.api_key || fullConfig.api_key === '***' || fullConfig.api_key === '') {
          showToast('Please configure AI settings first', 'error');
          showAIConfig();
          btn.disabled = false;
          btn.innerHTML = '<i class="bi bi-stars"></i> Run AI Text';
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
        
        showOCRMode('ai');
        showToast('AI OCR completed successfully! ✨', 'success');
      } catch (error) {
        showToast('AI OCR error: ' + error.message, 'error');
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-stars"></i> Run AI Text';
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
      const text = document.getElementById('extractedText').textContent;
      navigator.clipboard.writeText(text).then(() => {
        showToast('Text copied to clipboard!', 'success');
      }).catch(() => {
        showToast('Failed to copy text', 'error');
      });
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
                skip_incognito: document.getElementById('skipIncognitoCheckbox').checked,
                port: document.getElementById('portInput').value
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
      if (currentEntry) {
        const text = currentOCRMode === 'ai' && currentEntry.ai_text ? currentEntry.ai_text : currentEntry.text;
        document.getElementById('extractedText').textContent = text || 'No text available';
      }
    }

    // Electron UI Reset
    if (window.electronAPI) {
      window.electronAPI.onOpenSettings(() => {
          openSettings();
      });
      window.electronAPI.onResetUI(() => {
        // Close sidebar
        const sidebar = document.getElementById('sidebar');
        if (sidebar && sidebar.classList.contains('open')) {
          toggleSidebar();
        }
        
        // Close AI config
        closeAIConfig();
        
        // Close text popup
        closeTextPopup();
        
        // Close search results
        const searchResults = document.getElementById('searchResults');
        if (searchResults.classList.contains('show')) {
          searchResults.classList.remove('show');
          document.getElementById('searchInput').value = '';
          document.getElementById('searchIcon').style.display = 'block';
          document.getElementById('searchClear').style.display = 'none';
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


@app.route("/api/search")
def api_search():
    """API endpoint for search"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    
    entries = get_all_entries()
    embeddings = [entry.embedding for entry in entries]
    query_embedding = get_embedding(q)
    similarities = [cosine_similarity(query_embedding, emb) for emb in embeddings]
    
    query_lower = q.lower()
    scores = []
    for i, entry in enumerate(entries):
        semantic_score = similarities[i]
        text_lower = entry.text.lower()
        keyword_boost = 0
        
        if query_lower in text_lower:
            keyword_boost = 0.5
        else:
            query_words = query_lower.split()
            matched = sum(1 for word in query_words if word in text_lower)
            if matched > 0:
                keyword_boost = 0.3 * (matched / len(query_words))
        
        # Add recency bias (approx 0.003 points per year)
        recency_score = entry.timestamp / 1e10
        
        scores.append((i, semantic_score + keyword_boost + recency_score, keyword_boost > 0))
    
    scores.sort(key=lambda x: (x[2], x[1]), reverse=True)
    
    results = [
        {
            'timestamp': entries[idx].timestamp,
            'text': entries[idx].text[:200]
        }
        for idx, score, has_keyword in scores[:20]
    ]
    
    return jsonify(results)


@app.route("/api/sync")
def api_sync():
    """API endpoint to fetch new entries since a timestamp"""
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
        
    
    # Efficiently fetch only new entries using SQL filtering
    # This optimization prevents the server from reading the entire DB every 2 seconds
    new_entries = get_all_entries(min_timestamp=since)
    
    if not new_entries:
        return jsonify({'timestamps': [], 'entries': {}})
        
    # Timestamps (descending)
    new_timestamps = [e.timestamp for e in new_entries]
    
    # Entries dictionary
    new_entries_dict = {
        entry.timestamp: {
            'id': entry.id,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'words_coords': entry.words_coords,
            'ai_text': entry.ai_text,
            'ai_words_coords': entry.ai_words_coords if entry.ai_words_coords else []
        }
        for entry in new_entries
    }
    
    return jsonify({
        'timestamps': new_timestamps,
        'entries': new_entries_dict
    })



@app.route("/api/recording-status", methods=["GET"])
def get_recording_status():
    return jsonify({"paused": get_recording_paused()})


@app.route("/api/pause-recording", methods=["POST"])
def pause_recording():
    set_recording_paused(True)
    return jsonify({"paused": True})


@app.route("/api/resume-recording", methods=["POST"])
def resume_recording():
    set_recording_paused(False)
    return jsonify({"paused": False})


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
    entries = get_all_entries()
    # Convert entries to dict without embedding (numpy array)
    entries_dict = {
        entry.timestamp: {
            'id': entry.id,
            'app': entry.app,
            'title': entry.title,
            'text': entry.text,
            'timestamp': entry.timestamp,
            'words_coords': entry.words_coords,
            'ai_text': entry.ai_text,
            'ai_words_coords': entry.ai_words_coords if entry.ai_words_coords else []
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

    function renderTextOverlay() {
      textOverlay.innerHTML = '';
      if (!showOverlayCheckbox.checked || !currentEntry || !currentEntry.words_coords || currentEntry.words_coords.length === 0) {
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
        const original = entriesData[currentEntry.timestamp];
        
        if (mode === 'ai' && currentEntry.ai_text) {
          extractedText.textContent = currentEntry.ai_text;
          // Use AI coordinates if available, otherwise fallback to basic
          currentEntry.words_coords = (currentEntry.ai_words_coords && currentEntry.ai_words_coords.length > 0) 
            ? currentEntry.ai_words_coords 
            : original.words_coords;
        } else {
          extractedText.textContent = currentEntry.text;
          currentEntry.words_coords = original.words_coords;
        }
        renderTextOverlay();
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
    
    entries = get_all_entries()
    embeddings = [entry.embedding for entry in entries]
    query_embedding = get_embedding(q)
    similarities = [cosine_similarity(query_embedding, emb) for emb in embeddings]
    
    # Create a hybrid score: semantic similarity + keyword match boost
    query_lower = q.lower()
    scores = []
    for i, entry in enumerate(entries):
        semantic_score = similarities[i]
        
        # Boost score if query keywords are found in text
        text_lower = entry.text.lower()
        keyword_boost = 0
        
        # Exact phrase match gets highest boost
        if query_lower in text_lower:
            keyword_boost = 0.5
        else:
            # Check individual words
            query_words = query_lower.split()
            matched_words = sum(1 for word in query_words if word in text_lower)
            if matched_words > 0:
                keyword_boost = 0.3 * (matched_words / len(query_words))
        
        # Add recency bias (approx 0.003 points per year)
        recency_score = entry.timestamp / 1e10
        
        # Combined score
        final_score = semantic_score + keyword_boost + recency_score
        scores.append((i, final_score, keyword_boost > 0))
    
    # Sort by score, prioritizing entries with keyword matches
    scores.sort(key=lambda x: (x[2], x[1]), reverse=True)
    
    # Convert entries to dict without embedding (numpy array)
    sorted_entries = [
        {
            'id': entries[idx].id,
            'app': entries[idx].app,
            'title': entries[idx].title,
            'text': entries[idx].text,
            'timestamp': entries[idx].timestamp,
            'words_coords': entries[idx].words_coords,
            'ai_text': entries[idx].ai_text,
            'ai_words_coords': entries[idx].ai_words_coords if entries[idx].ai_words_coords else []
        }
        for idx, score, has_keyword in scores
    ]

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
        entries = get_all_entries()
        entry = next((e for e in entries if e.timestamp == timestamp), None)
        
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

    # Start the thread to record screenshots
    t = Thread(target=record_screenshots_thread)
    t.start()

    # Use Waitress for production
    from waitress import serve
    serve(app, host='127.0.0.1', port=configured_port, threads=6)
