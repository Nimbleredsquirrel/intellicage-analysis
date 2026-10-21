from nicegui import ui


DRAG_RESIZE = '''event => {
    if (event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    const handle = event.currentTarget;
    const frame = handle.closest('.single-chart-window');
    const start = frame.getBoundingClientRect();
    const x = event.clientX, y = event.clientY;
    const events = new AbortController();
    handle.setPointerCapture(event.pointerId);
    handle.addEventListener('pointermove', move => {
        const maxWidth = Math.min(4000, frame.parentElement.clientWidth);
        frame.style.width = Math.min(maxWidth, Math.max(300, start.width + move.clientX - x)) + 'px';
        frame.style.height = Math.min(4000, Math.max(300, start.height + move.clientY - y)) + 'px';
    }, {signal: events.signal});
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        handle.addEventListener(name, () => events.abort(), {signal: events.signal});
    }
}'''


def resize_handle():
    return ui.icon('south_east').classes('graph-resize-handle').props('role=button aria-label="resize graph"').on(
        'pointerdown', js_handler=DRAG_RESIZE).tooltip('drag to resize')
