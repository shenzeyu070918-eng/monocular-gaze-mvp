"""Non-predictive online One Euro and separate offline zero-phase smoothing.

One Euro follows Casiez/Roussel/Vogel: https://gery.casiez.net/1euro/ .
Coordinates are unbounded full-screen normalized positions; time is seconds on
ONE capture clock. No frame-rate, receive-time or inference-time substitution.
"""
import math
import numpy as np
from .config import FilterSettings, OfflineFilterSettings
from .devices import finite


def position(value):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 2:
        raise ValueError('position 必须是二维有限坐标')
    return np.array([finite(v, 'position') for v in value], dtype=float)


def alpha(cutoff, dt):
    return 1.0 / (1.0 + 1.0/(2*math.pi*cutoff*dt))


class OneEuro:
    """One scalar axis. reset(value) seeds the output and clears derivative history."""
    def __init__(self, settings=None):
        self.settings = settings or FilterSettings()
        self.clear()

    def clear(self):
        self.last_time = self.raw = self.filtered = None
        self.derivative = 0.0

    def reset(self, value, timestamp_s=None):
        value = finite(value, 'position')
        timestamp_s = None if timestamp_s is None else finite(timestamp_s, 'timestamp_s')
        self.raw = self.filtered = value
        self.derivative, self.last_time = 0.0, timestamp_s
        return value

    def update(self, value, timestamp_s):
        value, timestamp_s = finite(value,'position'), finite(timestamp_s,'timestamp_s')
        if self.last_time is None:
            return self.reset(value,timestamp_s)
        dt = timestamp_s-self.last_time
        if dt <= 0:
            raise ValueError('取帧时间戳必须严格递增')
        derivative = (value-self.raw)/dt
        da = alpha(self.settings.d_cutoff,dt)
        smoothed_derivative = da*derivative+(1-da)*self.derivative
        cutoff = self.settings.min_cutoff+self.settings.beta*abs(smoothed_derivative)
        a = alpha(cutoff,dt)
        output = a*value+(1-a)*self.filtered
        self.raw, self.filtered, self.derivative, self.last_time = value, output, smoothed_derivative, timestamp_s
        return float(output)


class OneEuro2D:
    def __init__(self, settings=None):
        self.axes = [OneEuro(settings),OneEuro(settings)]

    def clear(self):
        for axis in self.axes: axis.clear()

    def reset(self, value, timestamp_s=None):
        values = position(value)
        return [axis.reset(v,timestamp_s) for axis,v in zip(self.axes,values)]

    def update(self, value, timestamp_s):
        values, timestamp_s = position(value), finite(timestamp_s,'timestamp_s')
        if any(axis.last_time is not None and timestamp_s <= axis.last_time for axis in self.axes):
            raise ValueError('取帧时间戳必须严格递增')
        return [axis.update(v,timestamp_s) for axis,v in zip(self.axes,values)]


def zero_phase_smooth(positions, timestamps_s, settings=None):
    """Offline only: resample each continuous run, SOS forward/backward low-pass,
    then interpolate back to the original capture timestamps. Never bridge gaps.

    Too-short segments are returned unchanged with an explicit status; they are
    not advertised as zero-phase filtered. Boundary effects remain possible.
    """
    from scipy.signal import butter, sosfiltfilt
    settings = settings or OfflineFilterSettings()
    values = np.array([position(p) for p in positions],dtype=float).reshape((-1,2))
    times = np.array([finite(t,'timestamp_s') for t in timestamps_s])
    if len(times) != len(values) or (len(times)>1 and np.any(np.diff(times)<=0)):
        raise ValueError('坐标与严格递增的时间戳必须一一对应')
    output = values.copy()
    boundaries = [0,*list(np.flatnonzero(np.diff(times)>settings.max_gap_ms/1000)+1),len(times)] if len(times) else [0]
    segments=[]
    for start,end in zip(boundaries,boundaries[1:]):
        t,x = times[start:end],values[start:end]
        status='too_short_unfiltered'
        if len(t)>1:
            count = int(math.ceil((t[-1]-t[0])/float(np.median(np.diff(t)))))+1
            grid = np.linspace(t[0],t[-1],count)
            fs = (count-1)/(t[-1]-t[0])
            # Butterworth SOS default padding simplifies to 3*(order+1).
            if count>3*(settings.order+1):
                if settings.cutoff_hz >= fs/2:
                    raise ValueError('离线截止频率必须低于各段重采样频率的一半')
                sos = butter(settings.order,settings.cutoff_hz,fs=fs,output='sos')
                resampled=np.column_stack([np.interp(grid,t,x[:,i]) for i in range(2)])
                smoothed=sosfiltfilt(sos,resampled,axis=0)
                output[start:end]=np.column_stack([np.interp(t,grid,smoothed[:,i]) for i in range(2)])
                status='zero_phase'
        segments.append(dict(start_index=int(start),end_index=int(end),status=status))
    return dict(positions=output.tolist(),timestamps_s=times.tolist(),segments=segments)
