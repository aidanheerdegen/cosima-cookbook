import logging

from sqlalchemy import select
import xarray as xr

from . import database

def getvar(expt, variable, db, ncfile=None, n=None,
           start_time=None, end_time=None, chunks=None,
           time_units=None, offset=None):
    """For a given experiment, return an xarray DataArray containing the
    specified variable.

    If disambiguation based on filename is required, pass the ncfile
    argument.
    A subset of output data can be obtained by either
    restricting the number of results (use a negative value of n to
    get the latest n files), or by the time ranges spanned by the
    file.
    Override any chunking by passing a chunks dictionary.
    A time offset in days can also be applied.
    """

    conn, tables = database.create_database(db)

    # find candidate vars -- base query
    s = select([tables['ncfiles'].c.ncfile,
                tables['ncvars'].c.dimensions,
                tables['ncvars'].c.chunking,
                tables['ncfiles'].c.timeunits,
                tables['ncfiles'].c.calendar]) \
            .select_from(tables['ncvars'].join(tables['ncfiles'])) \
            .where(tables['ncvars'].c.variable == variable) \
            .where(tables['ncfiles'].c.experiment == expt) \
            .order_by(tables['ncfiles'].c.time_start)

    # further constraints
    if ncfile is not None:
        s = s.where(tables['ncfiles'].c.ncfile.like('%' + ncfile))
    if start_time is not None:
        s = s.where(tables['ncfiles'].c.time_end >= start_time)
    if end_time is not None:
        s = s.where(tables['ncfiles'].c.time_start <= end_time)

    ncfiles = conn.execute(s).fetchall()

    # restrict number of files directly
    if n is not None:
        if n > 0:
            ncfiles = ncfiles[:n]
        else:
            ncfiles = ncfiles[n:]

    # chunking -- use first row/file
    file_chunks = dict(zip(eval(ncfiles[0][1]), eval(ncfiles[0][2])))
    # apply caller overrides
    if chunks is not None:
        file_chunks.update(chunks)

    # the "dreaded" open_mfdata can actually be quite efficient
    # I found that it was important to "preprocess" to select only
    # the relevant variable, because chunking doesn't apply to
    # all variables present in the file
    ds = xr.open_mfdataset((f[0] for f in ncfiles), parallel=True,
                           chunks=file_chunks, autoclose=True,
                           decode_times=False,
                           preprocess=lambda d: d[variable].to_dataset())

    # handle time offsetting and decoding
    if 'time' in (c.lower() for c in ds.coords):
        calendar = ncfiles[0][4]

        # first rebase times onto new units if required
        if time_units is not None:
            dates = xr.conventions.times.decode_cf_datetime(ds.time, ncfiles[0][3], calendar)
            times = xr.conventions.times.encode_cf_datetime(dates, time_units, calendar)
            ds['time'] = times[0]
        else:
            time_units = ncfiles[0][3]

        # time offsetting - mimic one aspect of old behaviour by adding
        # a fixed number of days
        if offset is not None:
            ds['time'] += offset


        # decode time - we assume that we're getting units and a calendar from a file
        try:
            decoded_time = xr.conventions.times.decode_cf_datetime(ds.time, time_units, calendar)
            ds['time'] = decoded_time
        except Exception as e:
            logging.error('Unable to decode time: %s', e)

    return ds[variable]
