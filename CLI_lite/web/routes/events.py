"""全局事件总线 - 实现跨Tab联动"""
import json
import time
from flask import Blueprint, request, jsonify, Response, stream_with_context
from datetime import datetime


# 全局事件存储（模块级别，供蓝图和通知函数共享）
_latest_events = {
    "dag_created": None,
    "dag_updated": None,
    "log_updated": None,
    "preference_updated": None
}


def create_events_blueprint():
    """创建事件总线蓝图"""
    events = Blueprint('events', __name__, url_prefix='/api/events')
    
    @events.route('/publish', methods=['POST'])
    def publish_event():
        """发布事件"""
        data = request.json or {}
        event_type = data.get("type")
        event_data = data.get("data", {})
        
        if event_type not in _latest_events:
            return jsonify({"error": "Invalid event type"}), 400
        
        _latest_events[event_type] = {
            "timestamp": time.time(),
            "data": event_data
        }
        
        return jsonify({"status": "published", "type": event_type})
    
    @events.route('/latest', methods=['GET'])
    def get_latest():
        """获取所有最新事件"""
        return jsonify(_latest_events)
    
    @events.route('/stream')
    def event_stream():
        """SSE事件流"""
        def generate():
            last_check = time.time()
            while True:
                for event_type, event in _latest_events.items():
                    if event and event["timestamp"] > last_check:
                        yield f"data: {json.dumps({'type': event_type, 'data': event['data']}, ensure_ascii=False)}\n\n"
                        last_check = event["timestamp"]
                time.sleep(0.5)
        
        return Response(stream_with_context(generate()), mimetype='text/event-stream')
    
    return events


def notify_event(engine, event_type: str, data: dict):
    """通知事件（供其他模块调用）"""
    # 写入全局事件存储
    if event_type in _latest_events:
        _latest_events[event_type] = {
            "timestamp": time.time(),
            "data": data
        }
