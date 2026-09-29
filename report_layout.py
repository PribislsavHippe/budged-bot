"""Conservative detector for the known spreadsheet layout. No photos on disk."""
import io
from PIL import Image, ImageDraw

# Relative column boundaries, checked against visible grid lines before use.
EDGES=[93,161,221,463,532,611,696,777,858,939,1018,1099,1191]
COLUMNS=[('name',221,386),('dvd',20,93),('postcards',93,161),
         ('wine',613,778),('cocktails',778,940),('desserts',940,1100),('turnover',1100,1280)]


def detect(data):
    with Image.open(io.BytesIO(data)) as source:
        if source.width*source.height>12_000_000:
            raise ValueError('Слишком большое разрешение изображения.')
        image=source.convert('RGB')
    if not 1000<=image.width<=3000 or not 0.15<image.height/image.width<0.8:
        raise ValueError('Не удалось определить сетку отчёта. Пришли скриншот всей таблицы без полей.')
    scale=image.width/1280
    grey=image.convert('L');px=grey.load();w,h=image.size
    # Long horizontal rules in the name/actuals columns; reject photos with skew.
    xs=[round(x*scale) for x in (250,300,350,640,665,805,825,960,980,1120,1150)]
    lines=[]
    for y in range(h):
        if sum(px[x,y]<145 for x in xs)>=9:
            if not lines or y-lines[-1][-1]>2:lines.append([y])
            else:lines[-1].append(y)
    ys=[round(sum(group)/len(group)) for group in lines]
    runs=[];run=[]
    for a,b in zip(ys,ys[1:]):
        if 12*scale<=b-a<=24*scale:
            if not run:run=[a]
            run.append(b)
        else:
            if run:runs.append(run)
            run=[]
    if run:runs.append(run)
    if not runs:raise ValueError('Не удалось определить строки таблицы.')
    grid=max(runs,key=len)
    # Known header ends at about 67/1280 of image width. No guessing shifted crops.
    first=min(range(len(grid)),key=lambda i:abs(grid[i]-67*scale))
    if abs(grid[first]-67*scale)>4*scale or len(grid)-first<3:
        raise ValueError('Шапка таблицы не совпадает с поддерживаемым форматом.')
    grid=grid[first:]
    for edge in EDGES:
        x=round(edge*scale)
        if max(sum(px[min(w-1,max(0,x+d)),y]<170 for y in range(grid[0],grid[-1]))/(grid[-1]-grid[0]) for d in (-2,-1,0,1,2))<0.65:
            raise ValueError('Столбцы таблицы не совпадают с поддерживаемым форматом.')
    bands=list(zip(grid,grid[1:]))[:30]
    return image,scale,bands


def encode(image):
    output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()


def directory(data,relaxed=False):
    image,scale,bands=(relaxed_detect if relaxed else detect)(data)
    canvas=Image.new('RGB',(450,50*len(bands)),'white');draw=ImageDraw.Draw(canvas)
    for index,(top,bottom) in enumerate(bands):
        draw.text((5,index*50+15),str(index),fill='black')
        crop=image.crop((round(221*scale)+1,top+1,round(386*scale)-1,bottom))
        canvas.paste(crop.resize((330,36)),(70,index*50+7))
    return encode(canvas),len(bands)


def row_image(data,index,relaxed=False):
    image,scale,bands=(relaxed_detect if relaxed else detect)(data)
    if not 0<=index<len(bands):raise ValueError('Строка отсутствует.')
    top,bottom=bands[index]
    canvas=Image.new('RGB',(650,70*len(COLUMNS)),'white');draw=ImageDraw.Draw(canvas)
    for i,(key,left,right) in enumerate(COLUMNS):
        draw.text((8,i*70+20),key+(' | actual, percent' if i>=3 else ''),fill='black')
        crop=image.crop((round(left*scale)+1,top+1,min(image.width,round(right*scale)),bottom))
        canvas.paste(crop.resize(((right-left)*2,40)),(220,i*70+15))
    return encode(canvas)


def relaxed_detect(data):
    """Recover white margins and smaller/soft grids; still verify actual column rules.

    Header geometry may vary. Every retained band gets its own printed index, so
    skipping a heading/name cannot move the row subsequently sent to the model.
    """
    from PIL import ImageChops
    with Image.open(io.BytesIO(data)) as source:
        if source.width*source.height>12_000_000:raise ValueError('Слишком большое разрешение.')
        image=source.convert('RGB')
    diff=ImageChops.difference(image,Image.new('RGB',image.size,'white')).convert('L')
    box=diff.point(lambda v:255 if v>35 else 0).getbbox()
    if not box:raise ValueError('Пустое изображение.')
    image=image.crop(box);w,h=image.size;scale=w/1280
    if not 900<=w<=4000 or not .12<h/w<.9:raise ValueError('Не нашёл таблицу отчёта.')
    px=image.convert('L').load()
    xs=[min(w-1,round(x*scale)) for x in (250,300,350,640,665,805,825,960,980,1120,1150)]
    groups=[]
    for y in range(h):
        if sum(px[x,y]<175 for x in xs)>=9:
            if groups and y-groups[-1][-1]<=max(1,round(2*scale)):groups[-1].append(y)
            else:groups.append([y])
    ys=[round(sum(g)/len(g)) for g in groups]
    runs=[];run=[]
    for a,b in zip(ys,ys[1:]):
        if 11*scale<=b-a<=25*scale:
            if not run:run=[a]
            run.append(b)
        else:
            if run:runs.append(run)
            run=[]
    if run:runs.append(run)
    if not runs:raise ValueError('Не нашёл строки отчёта.')
    grid=max(runs,key=len)
    if len(grid)<8:raise ValueError('Недостаточно строк для проверки сетки.')
    if min(b-a for a,b in zip(grid,grid[1:]))<12:raise ValueError('Слишком мелкие строки. Нужен исходный файл.')
    # A screenshot may lose a few pixels on the left before Telegram resizes it.
    # Fit a single affine alignment to ALL visible column rules, not to text.
    observed=[];radius=max(4,round(18*scale))
    for edge in EDGES:
        expected=round(edge*scale)
        candidates=range(max(0,expected-radius),min(w,expected+radius+1))
        scores=[(sum(px[x,y]<170 for y in range(grid[0],grid[-1]))/(grid[-1]-grid[0]),x) for x in candidates]
        score,x=max(scores,key=lambda v:(v[0],-abs(v[1]-expected)))
        if score<.65:raise ValueError('Не совпали столбцы отчёта.')
        observed.append(x)
    mean_x=sum(EDGES)/len(EDGES);mean_y=sum(observed)/len(observed)
    factor=sum((x-mean_x)*(y-mean_y) for x,y in zip(EDGES,observed))/sum((x-mean_x)**2 for x in EDGES)
    offset=mean_y-factor*mean_x
    if not .96*scale<=factor<=1.04*scale or abs(offset)>22*scale or any(abs(factor*x+offset-y)>3*scale for x,y in zip(EDGES,observed)):
        raise ValueError('Столбцы отчёта прочитались неоднозначно.')
    # Canonical horizontal coordinates keep existing name/value crops aligned.
    image=image.transform((1280,h),Image.Transform.AFFINE,(factor,0,offset,0,1,0),
                          resample=Image.Resampling.BICUBIC,fillcolor='white')
    return image,1,list(zip(grid,grid[1:]))[:30]
