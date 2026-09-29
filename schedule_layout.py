"""Verified grid of the supplied schedule: names, blank spacer, day columns.

Never infer row coordinates if the grid does not match. Crops live only in memory.
"""
import io
from PIL import Image, ImageDraw
from report_layout import encode


def groups(values):
    runs=[]
    for n in values:
        if runs and n-runs[-1][-1]<=2:runs[-1].append(n)
        else:runs.append([n])
    return [round(sum(r)/len(r)) for r in runs]


def detect(data):
    with Image.open(io.BytesIO(data)) as source:
        if source.width*source.height>12_000_000:raise ValueError('Слишком большое разрешение графика.')
        image=source.convert('RGB')
    w,h=image.size
    if not 700<=w<=4000 or not .1<h/w<.65:raise ValueError('Пришли саму таблицу графика без полей и наклона.')
    px=image.load();scale=w/1280
    # The blank spacer is required; unlike grey-scale projections this rejects red days off.
    sample=range(round(90*scale),round(135*scale))
    ys=groups([y for y in range(h) if sum(max(px[x,y])<195 for x in sample)/len(sample)>.85])
    if len(ys)<5 or abs(ys[0]-17*scale)>3*scale:raise ValueError('Не разобрал сетку графика. Пришли всю таблицу без полей.')
    gaps=[b-a for a,b in zip(ys,ys[1:])]
    if any(not 13*scale<=gap<=21*scale for gap in gaps):raise ValueError('Не разобрал строки графика. Нужен ровный скриншот.')
    head=range(max(1,ys[0]-round(16*scale)),ys[1]-1)
    xs=groups([x for x in range(w) if sum(max(px[x,y])<195 for y in head)/len(head)>.7])
    if len(xs)<5 or abs(xs[0]-74*scale)>3*scale or abs(xs[1]-147*scale)>3*scale or w-xs[-1]>3*scale:
        raise ValueError('Не разобрал столбцы графика. Пришли таблицу целиком.')
    if any(not 50*scale<=b-a<=90*scale for a,b in zip(xs,xs[1:])):
        raise ValueError('Столбцы графика прочитались неоднозначно.')
    return image,xs,ys,list(zip(ys[1:],ys[2:]))


def directory(data):
    image,xs,ys,bands=detect(data)
    canvas=Image.new('RGB',(350,50*len(bands)),'white');draw=ImageDraw.Draw(canvas)
    for i,(top,bottom) in enumerate(bands):
        draw.text((8,i*50+18),str(i),fill='black')
        crop=image.crop((0,top+1,xs[0]-1,bottom))
        canvas.paste(crop.resize((280,40)),(55,i*50+5))
    return encode(canvas),len(bands)


def row_image(data,index):
    image,xs,ys,bands=detect(data)
    if type(index) is not int or not 0<=index<len(bands):raise ValueError('Не нашёл выбранную строку.')
    top,bottom=bands[index];columns=list(zip(xs[1:],xs[2:]))
    canvas=Image.new('RGB',(750,65+((len(columns)+2)//3)*110),'white')
    canvas.paste(image.crop((0,top+1,xs[0]-1,bottom)).resize((280,42)),(20,8))
    for i,(left,right) in enumerate(columns):
        x=(i%3)*250+10;y=65+(i//3)*110
        canvas.paste(image.crop((left+1,ys[0]+1,right,ys[1])).resize((220,38)),(x,y))
        canvas.paste(image.crop((left+1,top+1,right,bottom)).resize((220,42)),(x,y+42))
    return encode(canvas)
