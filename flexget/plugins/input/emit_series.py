from __future__ import unicode_literals, division, absolute_import
import logging

from sqlalchemy import desc

from flexget.entry import Entry
from flexget.plugin import register_plugin, DependencyError

log = logging.getLogger('emit_series')

try:
    from flexget.plugins.filter.series import SeriesTask, SeriesDatabase, Episode, Release
except ImportError as e:
    log.error(e.message)
    raise DependencyError(issued_by='emit_series', missing='series')


class EmitSeries(SeriesDatabase):
    """
    Emit next episode number from all series configured in this task.

    Supports only series enumerated by season, episode.
    """

    schema = {'type': 'boolean'}

    def search_strings(self, series, season, episode):
        return ['%s S%02dE%02d' % (series, season, episode),
                '%s %02dx%02d' % (series, season, episode)]

    def search_entry(self, series, season, episode, task, rerun=True):
        search_strings = self.search_strings(series.name, season, episode)
        entry = Entry(title=search_strings[0], url='',
                      search_strings=search_strings,
                      series_name=series.name,
                      series_season=season,
                      series_episode=episode,
                      series_id='S%02dE%02d' % (season, episode))
        if rerun:
            entry.on_complete(self.on_search_complete, task=task)
        return entry

    def on_task_input(self, task, config):
        if not config:
            return
        if not task.is_rerun:
            self.try_next_season = {}
        entries = []
        for seriestask in task.session.query(SeriesTask).filter(SeriesTask.name == task.name).all():
            series = seriestask.series
            if series.identified_by != 'ep':
                log.debug('cannot discover non-ep based series')
                continue

            latest = self.get_latest_download(series)
            if series.begin and (not latest or latest < series.begin):
                entries.append(self.search_entry(series, series.begin.season, series.begin.number, task))
            elif latest:
                if self.try_next_season.get(series.name):
                    entries.append(self.search_entry(series, latest.season + 1, 1, task))
                else:
                    episodes_this_season = (task.session.query(Episode).
                                            filter(Episode.series_id == series.id).
                                            filter(Episode.season == latest.season))
                    latest_ep_this_season = episodes_this_season.order_by(desc(Episode.number)).first()
                    downloaded_this_season = (episodes_this_season.join(Episode.releases).
                                              filter(Release.downloaded == True).all())
                    # Calculate the episodes we still need to get from this season
                    if series.begin and series.begin.season == latest.season:
                        eps_to_get = range(series.begin.number, latest_ep_this_season.number + 1)
                    else:
                        eps_to_get = range(1, latest_ep_this_season.number + 1)
                    for ep in downloaded_this_season:
                        try:
                            eps_to_get.remove(ep.number)
                        except ValueError:
                            pass
                    entries.extend(self.search_entry(series, latest.season, x, task, rerun=False) for x in eps_to_get)
                    # If we have already downloaded the latest known episode, try the next episode
                    if latest_ep_this_season.downloaded_releases:
                        entries.append(self.search_entry(series, latest.season, latest_ep_this_season.number + 1, task))
            else:
                continue

        return entries

    def on_search_complete(self, entry, task=None, **kwargs):
        if entry.accepted:
            # We accepted a result from this search, rerun the task to look for next ep
            self.try_next_season.pop(entry['series_name'], None)
            task.rerun()
        else:
            if entry['series_name'] not in self.try_next_season:
                self.try_next_season[entry['series_name']] = True
                task.rerun()
            else:
                # Don't try a second time
                self.try_next_season[entry['series_name']] = False


register_plugin(EmitSeries, 'emit_series', api_ver=2)
