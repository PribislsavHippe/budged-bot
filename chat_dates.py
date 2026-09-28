"""Human date input; ambiguous/future dates are clarified, never silently shifted."""
import re
from datetime import date, timedelta

MONTHS=('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')

def human_date(value):
    day=date.fromisoformat(value) if isinstance(value,str) else value
    return f'{day.day} {MONTHS[day.month-1]} {day.year}'


def parse_date(text,today):
    if not isinstance(text,str):raise ValueError('Укажи дату отчёта.')
    text=' '.join(text.lower().replace('ё','е').split()).strip(' .,!:')
    text=re.sub(r'^(?:(?:отчет|данные|дата|по|за|на|это|до)\s+)+','',text)
    text=re.sub(r'\s+(?:включительно|года|год|г\.)$','',text).strip()
    if text in ('сегодня','вчера','позавчера'):
        return today-timedelta(days={'сегодня':0,'вчера':1,'позавчера':2}[text])
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):result=date.fromisoformat(text)
        else:
            m=re.fullmatch(r'(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?',text)
            if m:
                year=int(m[3]) if m[3] else today.year
                if year<100:year+=2000
                result=date(year,int(m[2]),int(m[1]))
            else:
                words=re.fullmatch(r'(\d{1,2})(?:-?(?:го|е))?\s+([а-я]+)\.?(?:\s+(\d{4}))?',text)
                if words:
                    matches=[i+1 for i,name in enumerate(MONTHS) if words[2]==name or (len(words[2])>=3 and name.startswith(words[2]))]
                    if len(matches)!=1:raise ValueError()
                    result=date(int(words[3]) if words[3] else today.year,matches[0],int(words[1]))
                elif re.fullmatch(r'\d{1,2}(?:-?го)?',text):
                    result=date(today.year,today.month,int(re.match(r'\d+',text)[0]))
                else:raise ValueError()
        if not 2000<=result.year<=2100:raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('Не понял дату. Можно написать «13 сентября», «13.09» или «вчера».') from None
    if result>today:raise ValueError('Эта дата ещё впереди. Проверь день и месяц; если отчёт за прошлый год, добавь год.')
    return result
