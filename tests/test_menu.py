"""Подсказки по меню: разбор страниц PDF, поиск, карточка в чате. Данные выдуманные: настоящее меню — внутренний
документ компании, в публичный репозиторий оно не попадает."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
patch('supabase.create_client').start()

import menu_pdf  # noqa: E402
import menu_store  # noqa: E402
import menu_chat  # noqa: E402


def seg(x, top, text, size=10):
    return {'x0': x, 'x1': x + 6 * len(text), 'top': top, 'bottom': top + size, 'size': size, 'text': text}


def chars(x, top, text, step=6.0, gap=0.0, size=10):
    """Символы строки: step — ширина буквы, gap — разрядка между буквами."""
    out = []
    for c in text:
        out.append({'x0': x, 'x1': x + step, 'top': top, 'bottom': top + size, 'size': size, 'text': c})
        x += step + gap
    return out


class Page:
    def __init__(self, chars, width=465):
        self.chars, self.width = chars, width


class Segments(unittest.TestCase):
    def test_columns_split_and_letter_spacing_joined(self):
        page = Page(chars(20, 400, 'СОСТАВ') + chars(320, 400, 'АЛЛЕРГЕНЫ')
                    + chars(20, 500, 'СОСТАВ:', step=7, gap=3, size=15))
        found = [(round(s['x0']), s['text']) for s in menu_pdf.segments(page)]
        self.assertIn((20, 'СОСТАВ'), found)
        self.assertIn((320, 'АЛЛЕРГЕНЫ'), found)
        self.assertIn((20, 'СОСТАВ:'), found)

    def test_lone_bullet_glued_to_text(self):
        page = Page(chars(48, 100, '•') + chars(70, 100, 'Сливки'))
        self.assertEqual([s['text'] for s in menu_pdf.segments(page)], ['• Сливки'])


class Card(unittest.TestCase):
    def test_side_by_side_lists_and_continuation_lines(self):
        segs = [seg(20, 30, 'СУП ИЗ ТЫКВЫ', 25), seg(390, 60, '/ 300 г', 20), seg(20, 100, 'С ИМБИРНЫМ МАСЛОМ', 14),
                seg(25, 150, 'Густой суп из печёной тыквы.', 9), seg(25, 162, 'Подаём горячим.', 9),
                seg(25, 300, 'ПОДАЧА: Глубокая тарелка'), seg(330, 340, 'АЛЛЕРГЕНЫ:'), seg(20, 347, 'СОСТАВ:'),
                seg(330, 370, '• Лактоза'), seg(20, 371, '• Суп (тыква, сливки,'), seg(330, 384, '• Имбирь'),
                seg(20, 385, 'имбирь)'), seg(20, 399, '• Гренки')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual(card['name'], 'СУП ИЗ ТЫКВЫ')
        self.assertEqual(card['weight'], '300 г')
        self.assertEqual(card['subtitle'], 'С ИМБИРНЫМ МАСЛОМ')
        self.assertEqual(card['composition'], ['Суп (тыква, сливки, имбирь)', 'Гренки'])
        self.assertEqual(card['allergens'], ['Лактоза', 'Имбирь'])
        self.assertEqual(card['serving'], 'Глубокая тарелка')
        self.assertEqual(card['description'], ['Густой суп из печёной тыквы. Подаём горячим.'])

    def test_allergen_items_slightly_left_of_their_header(self):
        segs = [seg(20, 30, 'ПАСТА', 25), seg(337, 446, 'АЛЛЕРГЕНЫ:'), seg(20, 453, 'СОСТАВ:'),
                seg(331, 479, '• Глютен'), seg(21, 479, '• Паста')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual(card['allergens'], ['Глютен'])
        self.assertEqual(card['composition'], ['Паста'])

    def test_three_columns_of_a_cocktail(self):
        segs = [seg(23, 36, 'ГЛИНТВЕЙН', 36), seg(23, 78, 'MULLED WINE', 31), seg(418, 50, '7,0º', 17),
                seg(384, 89, '225 мл', 22), seg(26, 143, 'Сезонный глинтвейн на красном вине с пряностями.'),
                seg(26, 157, 'Согревает и пахнет праздником.'), seg(27, 400, 'СОСТАВ'), seg(175, 400, 'УКРАШЕНИЕ'),
                seg(323, 400, 'АЛЛЕРГЕНЫ'), seg(25, 422, '○ вино'), seg(173, 422, '○ апельсин'),
                seg(320, 422, '○ цитрус'), seg(25, 434, '○ корица')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual((card['name'], card['english'], card['strength'], card['weight']),
                         ('ГЛИНТВЕЙН', 'MULLED WINE', '7,0°', '225 мл'))
        self.assertEqual(card['composition'], ['вино', 'корица'])
        self.assertEqual(card['garnish'], ['апельсин'])
        self.assertEqual(card['allergens'], ['цитрус'])

    def test_russian_name_set_smaller_than_english_is_still_the_name(self):
        segs = [seg(23, 36, 'ЛИМОНАД', 32), seg(23, 79, 'LEMONADE', 36),
                seg(25, 142, 'Домашний лимонад на свежем соке.'), seg(27, 400, 'СОСТАВ'),
                seg(323, 400, 'АЛЛЕРГЕНЫ'), seg(25, 422, '○ лимон'), seg(320, 422, '○ цитрус')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual((card['name'], card['english']), ('ЛИМОНАД', 'LEMONADE'))

    def test_bulleted_header_and_bulleted_item_with_a_colon(self):
        segs = [seg(48, 40, 'Паста с уткой', 17.6), seg(42, 110, 'Описание блюда.', 14), seg(48, 195, 'Состав:', 13.7),
                seg(70, 212, '• Утка', 14), seg(70, 229, '• Украшение: базилик', 14),
                seg(70, 347, '• Аллергены:', 13.7), seg(70, 363, '• Глютен', 14)]
        card = menu_pdf.page_card(segs, 620)
        self.assertEqual(card['name'], 'Паста с уткой')
        self.assertEqual(card['composition'], ['Утка', 'Украшение: базилик'])
        self.assertEqual(card['allergens'], ['Глютен'])

    def test_letter_spaced_tail_new_mark_and_restaurant_note(self):
        segs = [seg(20, 20, 'КРОМЕ РЕСТОРАНА НА НАБЕРЕЖНОЙ', 9), seg(20, 60, 'ШЕЯ В АЗИАТСКОМ С Т И Л Е', 25),
                seg(20, 100, 'NEW С ПЕРЛОВКОЙ', 14), seg(20, 150, 'Текст.', 9), seg(20, 300, 'СОСТАВ:'),
                seg(300, 300, 'АЛЛЕРГЕНЫ:'), seg(20, 320, '• Шея'), seg(300, 320, '• Соя')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual(card['name'], 'ШЕЯ В АЗИАТСКОМ СТИЛЕ')
        self.assertTrue(card['new'])
        self.assertEqual(card['subtitle'], 'С ПЕРЛОВКОЙ')
        self.assertEqual(card['notes'], ['Кроме ресторана на набережной'])

    def test_page_without_allergens_is_not_a_dish(self):
        self.assertIsNone(menu_pdf.page_card([seg(20, 30, 'ГЛОССАРИЙ', 25), seg(20, 80, 'Пюре — это пюре.', 9)], 465))

    def test_bullet_in_the_middle_of_a_line_starts_the_next_column(self):
        page = Page(chars(20, 100, 'Хлеб (мука, вода,') + chars(124, 100, ' • Томаты'))
        self.assertEqual([s['text'] for s in menu_pdf.segments(page)], ['Хлеб (мука, вода,', '• Томаты'])

    def test_text_starting_off_the_page_is_ignored(self):
        page = Page(chars(-6, 100, 'АЛЛЕРГЕНЫ:') + chars(200, 100, 'СОСТАВ:'))
        self.assertEqual([s['text'] for s in menu_pdf.segments(page)], ['СОСТАВ:'])

    def test_word_starting_like_a_header_is_not_a_header(self):
        segs = [seg(20, 30, 'БУРГЕР', 25), seg(20, 100, 'Бургер уникален своими составляющими: соусом.', 9),
                seg(20, 300, 'СОСТАВ:'), seg(300, 300, 'АЛЛЕРГЕНЫ:'), seg(20, 320, '• Булочка'), seg(300, 320, '• Глютен')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual(card['composition'], ['Булочка'])
        self.assertEqual(card['description'], ['Бургер уникален своими составляющими: соусом.'])

    def test_note_far_right_of_a_list_is_not_part_of_it(self):
        segs = [seg(20, 30, 'ГРАНОЛА', 25), seg(20, 300, 'СОСТАВ:'), seg(20, 320, '• Овсяные хлопья'),
                seg(20, 400, 'АЛЛЕРГЕНЫ:'), seg(20, 420, '• Мед'), seg(300, 420, 'Предлагайте взять с собой', 11),
                seg(20, 434, '• Орехи'), seg(20, 448, 'ПОДАЧА: • Красный боул')]
        card = menu_pdf.page_card(segs, 465)
        self.assertEqual(card['allergens'], ['Мед', 'Орехи'])

    def test_several_dishes_stacked_on_one_page(self):
        segs = [seg(260, 70, 'ПИРОЖКИ', 36), seg(260, 140, 'С капустой', 13), seg(260, 170, 'Пирожок с капустой.', 13),
                seg(260, 220, 'СОСТАВ:', 13), seg(450, 220, 'АЛЛЕРГЕНЫ:', 13), seg(260, 240, '• Тесто', 12),
                seg(450, 240, '• Глютен', 12), seg(260, 256, '• Капуста', 12), seg(270, 300, 'ПОДАЧА:', 13),
                seg(270, 318, 'Тарелка', 12),
                seg(260, 400, 'С яйцом', 13), seg(260, 425, 'Пирожок с яйцом.', 13),
                seg(260, 470, 'СОСТАВ:', 13), seg(450, 470, 'АЛЛЕРГЕНЫ:', 13), seg(260, 490, '• Тесто', 12),
                seg(450, 490, '• Глютен', 12), seg(260, 506, '• Яйцо', 12), seg(450, 506, '• Яйцо', 12)]
        cards = menu_pdf.page_cards(segs, 620)
        self.assertEqual([c['name'] for c in cards], ['Пирожки — с капустой', 'Пирожки — с яйцом'])
        self.assertEqual(cards[1]['allergens'], ['Глютен', 'Яйцо'])
        self.assertEqual(cards[0]['composition'], ['Тесто', 'Капуста'])
        self.assertEqual(cards[1]['serving'], 'Тарелка')       # подача одна на страницу

    def test_second_dish_in_the_right_column_gets_its_own_card(self):
        segs = [seg(40, 120, 'Bloody Mary', 36), seg(40, 240, 'Классический коктейль для бранча.', 13),
                seg(355, 618, 'Вёрджин Мэри', 25), seg(355, 656, 'СОСТАВ:', 15), seg(40, 675, 'СОСТАВ:', 15),
                seg(198, 675, 'АЛЛЕРГЕНЫ:', 15), seg(355, 682, '• Томатный сок', 12), seg(40, 693, '• Текила', 12),
                seg(198, 701, '• Алкоголь', 12), seg(40, 709, '• Томатный сок', 12)]
        cards = menu_pdf.page_cards(segs, 620)
        self.assertEqual([c['name'] for c in cards], ['Bloody Mary', 'Вёрджин Мэри'])
        self.assertEqual(cards[0]['allergens'], ['Алкоголь'])
        self.assertEqual((cards[1]['composition'], cards[1]['allergens']), (['Томатный сок'], []))

    def test_section_names(self):
        self.assertEqual(menu_pdf.section('СЕЗОННОЕ SEASONAL'), 'Сезонное')
        self.assertEqual(menu_pdf.section('ОСЕНЬ-ЗИМА,26/27 СЛАДКОЕ'), 'Сладкое')
        self.assertEqual(menu_pdf.section('ЗАВТРАКИ И БРАНЧИ BREAKFAST & BRUNCH'), 'Завтраки и бранчи')
        self.assertIsNone(menu_pdf.section(None))


ITEMS = [{'id': str(n), 'card': c} for n, c in enumerate([
    {'name': 'ГОВЯЖЬИ ЩЕЧКИ БУРГИНЬОН', 'composition': ['Щечки (лук, вино)'], 'allergens': ['Лук/чеснок', 'Алкоголь']},
    {'name': 'ТАРТАР ИЗ ГОВЯДИНЫ', 'composition': ['Говядина', 'Лук шалот'], 'allergens': ['Лук']},
    {'name': 'ТАРТАР ИЗ ТУНЦА', 'composition': ['Тунец', 'Кунжут'], 'allergens': ['Рыба', 'Кунжут']},
    {'name': 'БОЛЬШОЙ СЭНДВИЧ', 'composition': ['Хлеб'], 'allergens': ['Глютен']},
    {'name': 'ЗАВТРАК НА СКОВОРОДЕ', 'composition': ['Яйца', 'Пиве-соус'], 'allergens': ['Яйцо']},
    {'name': 'НЕГРОНИ', 'english': 'NEGRONI', 'composition': ['джин'], 'allergens': ['цитрус']},
])]


class Search(unittest.TestCase):
    def name(self, query):
        found = menu_store.search(ITEMS, query)
        return found and (found[0], found[1]['card']['name'] if found[0] == 'card' else len(found[1]))

    def test_names_with_endings_and_typos(self):
        self.assertEqual(self.name('говяжьи щечки'), ('card', 'ГОВЯЖЬИ ЩЕЧКИ БУРГИНЬОН'))
        self.assertEqual(self.name('щеки'), ('card', 'ГОВЯЖЬИ ЩЕЧКИ БУРГИНЬОН'))
        self.assertEqual(self.name('тартар'), ('list', 2))
        self.assertEqual(self.name('negroni'), ('card', 'НЕГРОНИ'))

    def test_ingredients_strict_and_short_words_exact(self):
        self.assertEqual(self.name('лук'), ('list', 2))
        self.assertEqual(self.name('кунжут'), ('card', 'ТАРТАР ИЗ ТУНЦА'))
        self.assertIsNone(self.name('привет'))     # не «пиве»
        self.assertIsNone(self.name('как дела'))

    def test_one_word_of_a_phrase_is_not_a_menu_question(self):
        self.assertIsNone(self.name('спасибо большое'))
        self.assertIsNone(self.name('завтра работаю'))

    def test_without_still_lists_where_it_is(self):
        found = menu_store.search(ITEMS, 'без глютена')
        self.assertEqual(found[0], 'card')        # одна позиция — сразу карточка с аллергенами
        found = menu_store.search(ITEMS, 'без лука')
        self.assertEqual(found[0], 'list')
        self.assertIn('Где есть', found[2])
        self.assertIn('остальные', found[2])

    def test_allergen_question_looks_inside_not_at_names(self):
        items = [{'id': str(n), 'card': c} for n, c in enumerate([
            {'name': 'МОЛОКО С ПЕЧЕНЬЕМ', 'composition': ['Молоко'], 'allergens': ['Лактоза']},
            {'name': 'ЦЕЗАРЬ', 'composition': ['Сыр грана падано', 'Соус (желтки, сливки)'], 'allergens': ['Яйцо']},
            {'name': 'ПОКЕ', 'composition': ['Бобы эдамаме', 'Соус терияки'], 'allergens': ['Кунжут']},
            {'name': 'СУДАК', 'composition': ['Филе судака'], 'allergens': ['Лук']},
            {'name': 'СТЕЙК ИЗ КОРНЯ СЕЛЬДЕРЕЯ', 'composition': ['Сельдерей'], 'allergens': []},
            {'name': 'ГОВЯДИНА', 'composition': ['Сыровяленая говядина'], 'allergens': []},
        ])]
        found = menu_store.search(items, 'молоко')
        self.assertEqual((found[0], [i['card']['name'] for i in found[1]], found[3]),
                         ('list', ['МОЛОКО С ПЕЧЕНЬЕМ', 'ЦЕЗАРЬ'], 'milk'))     # не «сыровяленая»
        self.assertEqual(menu_store.search(items, 'соя')[1]['card']['name'], 'ПОКЕ')
        self.assertEqual(menu_store.search(items, 'есть ли яйца')[1]['card']['name'], 'ЦЕЗАРЬ')
        self.assertEqual(menu_store.search(items, 'рыба')[1]['card']['name'], 'СУДАК')
        self.assertEqual(menu_store.search(items, 'сельдерей')[1]['card']['name'], 'СТЕЙК ИЗ КОРНЯ СЕЛЬДЕРЕЯ')
        self.assertIsNone(menu_store.allergen_key('медальоны'))
        self.assertIn('остальные', menu_store.search(items, 'без молока')[2])

    def test_long_or_empty_text_is_not_searched(self):
        self.assertIsNone(self.name('и в на'))
        self.assertIsNone(self.name('очень ' * 20))


class Chat(unittest.TestCase):
    def test_allergen_list_comes_in_pages(self):
        matches = [{'id': str(n), 'card': {'name': f'БЛЮДО {n}'}} for n in range(23)]
        first = menu_chat._page(matches, 'egg', 0).inline_keyboard
        self.assertEqual((len(first), first[-1][0].text, first[-1][0].callback_data),
                         (11, 'Ещё 10 из 13', 'menu:g:egg:10'))
        last = menu_chat._page(matches, 'egg', 20).inline_keyboard
        self.assertEqual(len(last), 3)                       # три позиции, без «Ещё»

    def test_search_outcome_is_recorded_without_the_query(self):
        message = MagicMock()
        message.from_user.id = 1
        message.answer = AsyncMock()
        seen = []
        with patch.object(menu_store, 'allowed', AsyncMock(return_value=True)), \
                patch.object(menu_store, 'items', AsyncMock(return_value=ITEMS)), \
                patch.object(menu_chat, 'send_card', AsyncMock()), \
                patch('research.track', side_effect=lambda *a, **k: seen.append((a[1], k['step']))):
            for query in ('тартар', 'щечки', 'лук', 'привет', 'кунжут'):
                asyncio.run(menu_chat.answer(message, query))
        self.assertEqual(seen, [('menu_search', 'menu_list'), ('menu_search', 'menu_card'),
                                ('menu_search', 'menu_inside'), ('menu_search', 'menu_none'),
                                ('menu_search', 'menu_card')])
        self.assertEqual(menu_chat._kind(('list', [], 'Где есть соя', 'soy')), 'menu_allergen')

    def test_search_failure_is_recorded(self):
        message = MagicMock()
        message.from_user.id = 1
        with patch.object(menu_store, 'allowed', AsyncMock(return_value=True)), \
                patch.object(menu_store, 'items', AsyncMock(side_effect=RuntimeError)), \
                patch('diagnostics.failure'), patch('research.track') as track:
            self.assertFalse(asyncio.run(menu_chat.answer(message, 'щечки')))
        track.assert_called_once_with(1, 'menu_failed', step='menu_error', error_code='backend')

    def test_card_text_is_escaped_and_says_where_from(self):
        text = menu_chat.text({'name': 'СУП <B>', 'weight': '300 г', 'allergens': ['Лактоза'], 'composition': ['Тыква & сливки'],
                               'source': 'Основное меню', 'page': 6})
        self.assertIn('<b>Суп &lt;b&gt;</b> · 300 г', text)
        self.assertIn('лактоза', text)
        self.assertIn('Тыква &amp; сливки', text)
        self.assertIn('Основное меню, стр. 6', text)
        self.assertIn('в подсказке не указаны', menu_chat.text({'name': 'Беллини', 'page': 1}))

    def test_not_allowed_person_gets_the_old_answer(self):
        message = MagicMock()
        message.from_user.id = 5
        with patch.object(menu_store, 'allowed', AsyncMock(return_value=False)):
            self.assertFalse(asyncio.run(menu_chat.answer(message, 'щечки')))

    def test_list_answer_has_buttons_and_is_protected(self):
        message = MagicMock()
        message.from_user.id = 1
        message.answer = AsyncMock()
        with patch.object(menu_store, 'allowed', AsyncMock(return_value=True)), \
                patch.object(menu_store, 'items', AsyncMock(return_value=ITEMS)):
            self.assertTrue(asyncio.run(menu_chat.answer(message, 'тартар')))
        kwargs = message.answer.call_args.kwargs
        self.assertTrue(kwargs['protect_content'])
        self.assertEqual([r[0].callback_data for r in kwargs['reply_markup'].inline_keyboard], ['menu:c:1', 'menu:c:2'])

    def test_long_card_goes_as_photo_with_short_caption_and_full_text(self):
        message = MagicMock()
        message.answer, message.answer_photo = AsyncMock(), AsyncMock()
        message.answer_photo.return_value.photo = [MagicMock(file_id='F')]
        item = {'id': 'x', 'photo_id': 'p', 'card': {'name': 'Салат', 'allergens': ['Рыба'], 'page': 2,
                                                       'composition': ['ингредиент ' * 8] * 15, 'description': ['Текст']}}
        with patch.object(menu_store, 'photo', AsyncMock(return_value=(None, b'jpg'))), \
                patch.object(menu_store, 'remember_file_id', AsyncMock()) as remember:
            asyncio.run(menu_chat.send_card(message, item))
        caption = message.answer_photo.call_args.kwargs['caption']
        self.assertLessEqual(len(caption), menu_chat.CAPTION)
        self.assertNotIn('Состав', caption)
        self.assertIn('Состав', message.answer.call_args.args[0])
        self.assertEqual(message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'menu:d:x')
        remember.assert_awaited_once_with('p', 'F')

    def test_pdf_in_chat_is_not_parsed_on_the_server_unless_turned_on(self):
        message = MagicMock()
        message.document.file_name = 'Меню.pdf'
        message.answer = AsyncMock()
        with patch.dict(os.environ, {'MENU_PDF_UPLOAD': ''}), patch.object(menu_store, 'import_pdf', AsyncMock()) as run:
            asyncio.run(menu_chat.upload(message))
        run.assert_not_awaited()
        self.assertEqual(message.answer.call_args.args[0], menu_chat.UPLOAD_OFF)

    def test_text_without_amount_reaches_the_menu_before_the_generic_reply(self):
        import handlers
        import ux_chat
        message = MagicMock()
        message.text, message.forward_origin = 'говяжьи щечки', None
        message.from_user.id = 1
        message.answer = AsyncMock()
        with patch.object(handlers.db, 'get_or_create_user', AsyncMock(return_value={'onboarded': True})), \
                patch.object(menu_chat, 'answer', AsyncMock(return_value=True)) as menu, \
                patch.object(ux_chat, 'understand', AsyncMock(return_value=True)) as generic:
            asyncio.run(handlers.handle_text(message, MagicMock()))
        menu.assert_awaited_once()
        generic.assert_not_awaited()
        message.answer.assert_not_awaited()

    def test_source_name(self):
        self.assertEqual(menu_store.source_name('Основное меню.PDF'), 'Основное меню')
        self.assertEqual(menu_store.source_name(None), 'Меню')


if __name__ == '__main__':
    unittest.main()
