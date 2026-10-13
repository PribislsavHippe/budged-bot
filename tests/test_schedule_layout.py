"""Grid regression checks with synthetic tables, without employee images."""
import io
import unittest

from PIL import Image, ImageDraw

import schedule_layout


CURRENT_EDGES = [70, 139, 208, 278, 348, 417, 486, 556, 632,
                 708, 783, 859, 934, 1004, 1072, 1141, 1210, 1279]


def table(edges=CURRENT_EDGES, width=1280, spacer=True):
    image = Image.new('RGB', (1280, 241), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 17, 1279, 31), fill='#ffd6a5')
    draw.rectangle((edges[0]+1, 33, edges[1]-1, 207), fill='#e0efcf')
    for row in range(10):
        top = 33 + row*16
        draw.text((3, top), 'Person '+str(row), fill='black')
        for col in range(16):
            left, right = edges[col+1], edges[col+2]
            if (row+col)%3 == 0:
                draw.rectangle((left+1, top, right-1, top+14), fill='red')
            else:
                draw.text((left+20, top), '11', fill='black')
    for y in range(16, 209, 16):
        draw.line((0, y, 1279, y), fill='#666666')
    for x in edges:
        draw.line((x, 0, x, 208), fill='#666666')
    if not spacer:
        draw.rectangle((edges[0]+1, 33, edges[1]-1, 207), fill='black')
    if width != 1280:
        image = image.resize((width, round(241*width/1280)))
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=90)
    return output.getvalue()


class ScheduleLayoutTests(unittest.TestCase):
    def test_whole_image_keeps_portrait_tables_and_surrounding_margins(self):
        for size in ((700,1100),(2200,180),(1500,600)):
            with self.subTest(size=size):
                source=Image.new('RGB',size,'white')
                draw=ImageDraw.Draw(source)
                draw.rectangle((20,30,size[0]-20,min(size[1]-20,400)),outline='black')
                output=io.BytesIO();source.save(output,format='PNG')
                with Image.open(io.BytesIO(schedule_layout.whole_image(output.getvalue()))) as prepared:
                    self.assertEqual(prepared.size,size)
                    self.assertEqual(prepared.format,'JPEG')

    def test_whole_image_applies_phone_exif_rotation(self):
        source=Image.new('RGB',(240,120),'white')
        exif=source.getexif();exif[274]=6
        output=io.BytesIO();source.save(output,format='JPEG',exif=exif)
        with Image.open(io.BytesIO(schedule_layout.whole_image(output.getvalue()))) as prepared:
            self.assertEqual(prepared.size,(120,240))

    def test_current_widths_preserve_names_spacer_and_all_days(self):
        for width in (960, 1280, 1920):
            with self.subTest(width=width):
                image, xs, ys, bands = schedule_layout.detect(table(width=width))
                self.assertEqual(len(xs), 18)
                self.assertEqual(len(bands), 11)
                self.assertAlmostEqual(xs[0]/width, 70/1280, delta=2/width)
                self.assertAlmostEqual(xs[1]/width, 139/1280, delta=2/width)
                directory, count = schedule_layout.directory(table(width=width))
                self.assertEqual(count, 11)
                with Image.open(io.BytesIO(directory)) as names:
                    self.assertEqual(names.size, (350, 550))
                with Image.open(io.BytesIO(schedule_layout.row_image(table(width=width), 7))) as row:
                    self.assertEqual(row.size, (750, 725))

    def test_previous_column_widths_still_work(self):
        edges = [74, 147] + [round(147+(1279-147)*i/16) for i in range(1, 17)]
        self.assertEqual(len(schedule_layout.detect(table(edges))[3]), 11)

    def test_nonempty_spacer_is_rejected(self):
        with self.assertRaises(ValueError):
            schedule_layout.detect(table(spacer=False))

    def test_missing_grid_is_rejected(self):
        image = Image.new('RGB', (1280, 241), 'white')
        output = io.BytesIO(); image.save(output, format='PNG')
        with self.assertRaises(ValueError):
            schedule_layout.detect(output.getvalue())
