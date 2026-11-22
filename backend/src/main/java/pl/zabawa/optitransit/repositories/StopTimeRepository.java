package pl.zabawa.optitransit.repositories;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import pl.zabawa.optitransit.models.entities.StopTimeEntity;
import pl.zabawa.optitransit.models.entities.StopTimeId;

import java.util.List;

@Repository
public interface StopTimeRepository extends JpaRepository<StopTimeEntity, StopTimeId> {

    @Query(value = """
            SELECT 
                r.route_short_name AS line,
                COALESCE(t.trip_headsign, '') AS headsign,
                r.route_type AS routeType,
                COALESCE(r.route_color, '000000') AS routeColor,
                COALESCE(r.route_text_color, 'FFFFFF') AS routeTextColor,
                st.departure_time AS departureTime,
                t.wheelchair_accessible AS wheelchairAccessible,
                t.trip_id AS tripId,
                st.stop_id AS stopId
            FROM gtfs_stop_times st
            JOIN gtfs_trips t ON st.trip_id = t.trip_id
            JOIN gtfs_routes r ON t.route_id = r.route_id
            WHERE st.stop_id = :stopId 
              AND st.departure_time >= :time
            ORDER BY st.departure_time ASC
            LIMIT :limit
            """, nativeQuery = true)
    List<DepartureProjection> findUpcomingDepartures(
            @Param("stopId") String stopId,
            @Param("time") String time,
            @Param("limit") int limit
    );

    interface DepartureProjection {
        String getLine();
        String getHeadsign();
        Integer getRouteType();
        String getRouteColor();
        String getRouteTextColor();
        String getDepartureTime();
        Integer getWheelchairAccessible();
        String getTripId();
        String getStopId();
    }
}
