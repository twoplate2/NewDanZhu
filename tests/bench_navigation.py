"""实际 Kivy 菜单按钮、子窗口关闭和测试结束返回；测试存档全部隔离。"""
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from bench_smoke import _boot


def main():
    root, clock, Clock = _boot()
    clock.time_ns = lambda: int(clock.t * 1e9)
    from kivy.app import App
    from kivy.core.window import Window
    from kivy.uix.button import Button
    from danzhu.ui.widgets import RotPopup, LandLayer, _POPUP_N

    def pump():
        for _ in range(20):
            clock.t += 1/60
            Clock.tick()

    def popups():
        return [p for p in Window.children if isinstance(p, RotPopup) and p._is_open]

    def click(popup, text):
        found = [w for w in popup.content.walk() if isinstance(w, Button) and w.text == text]
        assert len(found) == 1, (text, len(found))
        found[0].dispatch('on_release')
        pump()

    def menu_only():
        pump()
        menu = root._bench_menu_popup
        assert popups() == [menu], [p.title for p in popups()]
        assert not getattr(root, '_bench_menu_return', False)
        assert root._show_bench_menu() is menu  # 重复入口不叠菜单。
        return menu

    with tempfile.TemporaryDirectory(prefix='danzhu_navigation_') as directory, \
            patch.object(App, 'get_running_app', return_value=SimpleNamespace(user_data_dir=directory, layer=None)):
        root._show_bench_menu()
        for label, close in (('快速测试历史', '关闭'), ('高压测试历史', '关闭'),
                             ('帧率测试历史', '关闭'), ('游戏信息', '确定'),
                             ('帧率上限设定', '取消'), ('帧率上限设定', '确定')):
            menu = menu_only()
            click(menu, label)
            assert len(popups()) == 2 and menu._is_open, label
            click(popups()[0], close)
            assert menu_only() is menu
        print('PASS: all five child entries, settings cancel/confirm')

        for way in ('keyboard', 'outside'):
            menu = menu_only()
            click(menu, '游戏信息')
            if way == 'keyboard':
                Window.dispatch('on_keyboard', 27, 0, '', [])
            else:
                touch = SimpleNamespace(pos=(-10, -10))
                popups()[0].on_touch_down(touch)
                popups()[0].on_touch_up(touch)
            pump()
            assert menu_only() is menu
        print('PASS: Escape/back and outside touch close only child')

        # 历史详情在列表上面；关闭详情回列表，关闭列表回主菜单。
        root._write_playtest_history([dict(time='2026-11-12 12:00', settings={}, metrics={})])
        menu = menu_only()
        click(menu, '帧率测试历史')
        history = popups()[0]
        click(history, '详情')
        assert len(popups()) == 3
        click(popups()[0], '关闭')
        assert popups() == [history, menu]
        click(history, '清空历史')
        assert len(popups()) == 2
        click(popups()[0], '取消')
        menu_only()
        click(menu, '帧率测试历史')
        click(popups()[0], '清空历史')
        click(popups()[0], '确定清空')
        assert len(popups()) == 2
        click(popups()[0], '关闭')
        menu_only()
        print('PASS: history detail and clear cancel/confirm')

        # 执行真正的开始/结果界面代码，只跳过耗时的采样与工作线程。
        with patch.object(root, '_await_prebake'):
            click(menu_only(), '开始快速测试')
            assert not popups() and root.game._bench_running
            root._bench_done(1, 60, [6000], [0.01])
            pump()
            assert len(popups()) == 1
            click(popups()[0], '关闭')
            menu_only()
        with patch.object(root, '_wait_idle_then_hp'):
            click(menu_only(), '开始高压测试')
            assert not popups() and root._hp_running
            root._hp_fps = [6000, 5800]
            root._hp_done()
            pump()
            assert len(popups()) == 1
            click(popups()[0], '关闭')
            menu_only()
        print('PASS: quick/stress tests hide menu; result close restores it')

        # 入口拒绝启动，也必须保留回到菜单的路径。
        root.game.state = 'flying'
        click(menu_only(), '开始帧率测试')
        menu_only()
        root.game.park_ball(reroll=False)
        root.game_area.win_fx._abort()
        click(menu_only(), '开始帧率测试')
        assert not popups() and root._playtest is not None
        root._stop_playtest()
        pump()
        assert len(popups()) == 1
        click(popups()[0], '关闭')
        assert root._playtest is None
        menu_only()
        # 切后台不弹总结，回来点停止后的“关闭”也能回菜单。
        click(menu_only(), '开始帧率测试')
        root._stop_playtest('paused')
        root._playtest_stop_btn.dispatch('on_release')
        menu_only()
        print('PASS: frame test rejected/finished/paused paths')

        # 不触碰音效缓存，模拟重放中的忙标记；结果窗口使用真实入口。
        with patch.object(root, '_replay_cold_start', side_effect=lambda: setattr(root, '_replay_busy', True)):
            click(menu_only(), '游戏信息')
            click(popups()[0], '重放冷启动')
            assert not popups()
            root._replay_busy = False
            root._show_replay_detail()
            pump()
            click(popups()[0], '确定')
            menu_only()
        print('PASS: replay hides menu and detail returns to menu')

        # 主菜单主动关闭后不复活；非菜单入口关闭也不额外打开菜单。
        menu_only()
        Window.dispatch('on_keyboard', 27, 0, '', [])
        pump()
        assert not popups()
        root._show_startup_info()
        pump()
        click(popups()[0], '确定')
        root._restore_bench_menu()
        pump()
        assert not popups()
        assert _POPUP_N[0] == 0
        print('PASS: explicit menu exit and standalone popup stay closed')

        # 横屏弹窗挂旋转层，父菜单和子窗口应保持同一层次关系。
        layer = LandLayer()
        layer.angle = 90
        with patch.object(App, 'get_running_app', return_value=SimpleNamespace(layer=layer)):
            menu = root._show_bench_menu()
            click(menu, '帧率上限设定')
            children = [p for p in layer.children if isinstance(p, RotPopup) and p._is_open]
            assert len(children) == 2 and children[-1] is menu
            click(children[0], '取消')
            assert [p for p in layer.children if isinstance(p, RotPopup)] == [menu]
            menu.dismiss(animation=False)
        assert _POPUP_N[0] == 0
        print('PASS: rotated popup parent and child close without leaked modal count')


if __name__ == '__main__':
    main()
