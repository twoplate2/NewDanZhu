"""右上角常规消息禁用。"""
from bench_smoke import _boot


def main():
    root,_,_= _boot()
    from danzhu.ui.text import _build_texwarm
    assert root.status_lbl.text==''
    root._status_message('中奖! +200 (x2)');assert root.status_lbl.text==''
    root._status_message('保存失败',important=True);assert root.status_lbl.text=='保存失败'
    root._status_message('发射!');assert root.status_lbl.text=='保存失败'
    root._status_message('');assert root.status_lbl.text==''
    assert all(item[0] is not root.status_lbl for item in _build_texwarm(root,100))
    print('PASS: routine status suppressed, important errors retained, no status prewarm')


if __name__=='__main__':main()
