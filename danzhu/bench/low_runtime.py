"""Application submission capture. No Kivy dependency or display-present claims."""
import json
import hashlib
import math
import time
import uuid

ACTIVE = [None]


class Capture:
    def __init__(self, mode='fixed_five', probe='light', target=5, watchdog_sec=120.0,
                 max_records=20000, clock=time.perf_counter, meta=None):
        if mode not in ('fixed_five', 'normal') or probe not in ('light', 'full'):
            raise ValueError('unknown capture mode/probe')
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + watchdog_sec
        self.mode, self.probe, self.target = mode, probe, target
        self.max_records = max_records
        self.meta = dict(meta or {}, session_id=uuid.uuid4().hex, mode=mode, probe=probe,
                         interval_unit='ms', watchdog_sec=watchdog_sec,
                         timing_source='on_flip_application_pre_submit',
                         present_source='unavailable', scene_schema='danzhu.scene.v1')
        self.frames, self.events, self.shots = [], [], []
        self.reasons = []
        self.lost = 0
        self.active = True
        self.loop_id = self.frame_id = self.draw_id = self.flip_id = 0
        self.submitted_loop = -1
        self.draw_loop = -1
        self.prev_flip = None
        self.wall_ms = self.cpu_ms = 0.0
        self.calls = 0
        self.swap = None
        self.summary = None

    @property
    def shot(self):
        return self.shots[-1] if self.shots else None

    def append(self, dest, value):
        if len(self.frames) + len(self.events) < self.max_records:
            dest.append(value)
        else:
            self.lost += 1
            self.invalidate('buffer_overflow')

    def invalidate(self, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)

    def event(self, name, **data):
        if self.active:
            self.append(self.events, dict(event=name, monotonic_s=self.clock(),
                        shot_id=self.shot['shot_id'] if self.shot else None, **data))

    def plan(self, multiplier=None, variant=None, seed=None):
        if self.mode == 'fixed_five' and self.shot and self.shot['state'] != 'visual_finished':
            self.invalidate('overlapping_shot')
        self.shots.append(dict(shot_id=len(self.shots)+1, state='planned',
                               multiplier=multiplier, pile_variant=variant, seed=seed,
                               terminal_loop=None, pending=set()))
        self.event('planned', multiplier=multiplier, pile_variant=variant, seed=seed)

    def scheduled(self):
        self.shot['state'] = 'scheduled'
        self.event('scheduled')

    def launched(self):
        if self.mode == 'normal':
            self.plan()
        if not self.shot or self.shot['state'] not in ('planned', 'scheduled'):
            self.invalidate('duplicate_or_unplanned_launch')
            return
        self.shot['state'] = 'launched'
        self.event('launched')

    def settled(self, multiplier):
        if not self.shot or self.shot['state'] != 'launched':
            self.invalidate('duplicate_or_unlaunched_settle')
            return
        self.shot['state'] = 'settled'
        self.shot['multiplier'] = multiplier
        self.event('settled', multiplier=multiplier)

    def task(self, name, pending, shot=None):
        shot = shot or self.shot
        if shot:
            if pending:
                shot['pending'].add(name)
            else:
                shot['pending'].discard(name)
            self.event('task_scheduled' if pending else 'task_finished', task=name)

    def frame_end(self, game, area, wall_ms, cpu_ms):
        if not self.active:
            return
        self.frame_id += 1
        self.calls += 1
        self.wall_ms += wall_ms
        self.cpu_ms += cpu_ms
        fx = getattr(area, 'win_fx', None)
        busy = (getattr(fx, 'mode', 'idle') != 'idle' or getattr(fx, '_dirty', False)
                or any(e.get('kind') == 'big' for e in getattr(area, '_effects', ())))
        busy = busy or getattr(game, '_anim_pending', False)
        if getattr(game, '_anim_start_time', 0) + getattr(game, '_anim_dur', 0) > game.now:
            busy = True
        for shot in self.shots:
            if shot['state'] != 'settled':
                continue
            if game.state != 'ready' or busy or shot['pending']:
                shot['terminal_loop'] = None
            elif shot['terminal_loop'] is None:
                shot['terminal_loop'] = self.frame_id
                self.event('visual_terminal', clock_id=self.loop_id, frame_id=self.frame_id)

    def draw(self):
        self.draw_id += 1
        self.draw_loop = self.frame_id

    def submit(self, start, end):
        self.swap = dict(swap_start_s=start, swap_end_s=end,
                         swap_interval_flip_id=self.flip_id)
        self.submitted_loop = self.draw_loop
        for shot in self.shots:
            if shot['terminal_loop'] is not None and self.submitted_loop >= shot['terminal_loop']:
                if shot['state'] != 'visual_finished':
                    shot['state'] = 'visual_finished'
                    self.event('visual_finished', finished_shot_id=shot['shot_id'],
                               clock_id=self.loop_id, frame_id=self.submitted_loop, flip_id=self.flip_id)

    def flip(self, phase, thread_cpu_ms=0.0, process_cpu_ms=0.0, diagnostics=None):
        now = self.clock()
        self.flip_id += 1
        if self.prev_flip is not None:
            interval_ms = (now-self.prev_flip)*1000
            if not math.isfinite(interval_ms) or interval_ms <= 0:
                self.invalidate('invalid_monotonic_interval')
                self.prev_flip = now
                self.calls = 0
                self.wall_ms = self.cpu_ms = 0.0
                self.swap = None
                return
            row = dict(interval_ms=interval_ms, phase=phase,
                       shot_id=self.shot['shot_id'] if self.shot else None,
                       monotonic_s=now, clock_id=self.loop_id, frame_id=self.frame_id, draw_id=self.draw_id,
                       flip_id=self.flip_id, frame_calls=self.calls,
                       frame_wall_ms=self.wall_ms, frame_cpu_ms=self.cpu_ms,
                       thread_cpu_ms=thread_cpu_ms, process_cpu_ms=process_cpu_ms)
            row.update(self.swap or dict(swap_start_s=None, swap_end_s=None,
                                         swap_interval_flip_id=None))
            if self.probe == 'full':
                row['diagnostics_inclusive_ms'] = diagnostics or {}
            self.append(self.frames, row)
        self.prev_flip = now
        self.calls = 0
        self.wall_ms = self.cpu_ms = 0.0
        self.swap = None

    def expired(self):
        return self.active and self.clock() >= self.deadline

    def complete(self):
        return bool(self.shots) and (self.mode == 'normal' or len(self.shots) == self.target) and all(
            s['state'] == 'visual_finished' for s in self.shots)

    def freeze(self, reason=None):
        if not self.active:
            return self.summary
        if reason:
            self.invalidate(reason)
        complete = self.complete()
        if not complete:
            self.invalidate('incomplete_lifecycle')
        if not self.frames:
            self.invalidate('no_intervals')
        self.active = False
        self.meta['shots'] = [dict((k, v) for k, v in s.items() if k != 'pending') for s in self.shots]
        scene = [{k: shot[k] for k in ('shot_id', 'multiplier', 'pile_variant', 'seed')}
                 for shot in self.shots]
        pile = [{k: v for k, v in event.items() if k not in ('monotonic_s', 'event')}
                for event in self.events if event['event'] == 'pile_started']
        self.meta['scenario'] = dict(version=1, signature=hashlib.sha256(
            json.dumps(dict(shots=scene, piles=pile), sort_keys=True).encode('utf-8')).hexdigest(), shots=scene)
        self.summary = dict(complete=complete, valid=complete and not self.reasons,
                            invalid_reasons=list(self.reasons), lost_records=self.lost,
                            frame_count=len(self.frames),
                            duration_ms=sum(f['interval_ms'] for f in self.frames))
        return self.summary

    def document(self):
        if self.active:
            raise RuntimeError('freeze capture before export')
        return dict(schema='danzhu.low.v1', meta=self.meta, events=self.events,
                    frames=self.frames, summary=self.summary)

    def export(self, path):
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(self.document(), stream, ensure_ascii=False, indent=2)
