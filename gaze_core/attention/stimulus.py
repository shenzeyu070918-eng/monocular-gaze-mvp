"""Image fit/mapping in full built-in screen logical coordinates."""
from dataclasses import dataclass
from gaze_core.demo_coordinates import DemoCoordinates
from gaze_core.devices import finite

@dataclass(frozen=True)
class StimulusMapping:
    coordinates: DemoCoordinates
    image_size: tuple

    @property
    def image_rect(self):
        width,height=self.coordinates.window_size;iw,ih=self.image_size
        if iw<=0 or ih<=0:raise ValueError('刺激图片尺寸无效')
        scale=min(width/iw,height/ih);w,h=iw*scale,ih*scale
        return ((width-w)/2,(height-h)/2,w,h)

    def map(self,point):
        if not self.coordinates.ready:return None
        x,y=self.coordinates.screen_to_window(point);left,top,w,h=self.image_rect
        u,v=(x-left)/max(1,w-1),(y-top)/max(1,h-1)
        return [float(u),float(v)] if 0<=u<=1 and 0<=v<=1 else None

    def normalized_rect(self):
        left,top,w,h=self.image_rect
        a=self.coordinates.window_to_screen((left,top));b=self.coordinates.window_to_screen((left+w-1,top+h-1))
        return [*a,*b]

    def kernel(self,scale):
        x0,y0,x1,y1=self.normalized_rect()
        return dict(scale,sigma_x=finite(scale['sigma_x'],'sigma_x',positive=True)/(x1-x0),sigma_y=finite(scale['sigma_y'],'sigma_y',positive=True)/(y1-y0))
