"""Flask Web应用 - 页面路由 (SPA)"""
from flask import Blueprint, render_template


def create_page_blueprint():
    """创建页面蓝图"""
    pages = Blueprint('pages', __name__)

    @pages.route('/')
    @pages.route('/dag')
    @pages.route('/logs')
    @pages.route('/preferences')
    @pages.route('/config')
    @pages.route('/test')
    def index():
        """SPA 入口 - 所有路由返回同一个页面"""
        return render_template('index.html')

    return pages
