package pl.zabawa.optitransit.repositories;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;
import pl.zabawa.optitransit.models.entities.StopEntity;

import java.util.List;

@Repository
public interface StopRepository extends JpaRepository<StopEntity, String> {

    List<StopEntity> findByNameContainingIgnoreCase(String name, Pageable pageable);

    @Query(value = """
            SELECT 
                s.stop_id AS id,
                s.stop_name AS name,
                s.stop_code AS code,
                s.platform_code AS platformCode,
                s.stop_lat AS latitude,
                s.stop_lon AS longitude,
                s.town_name AS townName,
                s.wheelchair_boarding AS wheelchairBoarding,
                ROUND((6371000 * acos(
                    LEAST(1.0, GREATEST(-1.0, 
                        cos(radians(:lat)) * cos(radians(s.stop_lat)) * cos(radians(s.stop_lon) - radians(:lon)) + 
                        sin(radians(:lat)) * sin(radians(s.stop_lat))
                    ))
                ))::numeric, 1) AS distanceMeters
            FROM gtfs_stops s
            WHERE (6371000 * acos(
                    LEAST(1.0, GREATEST(-1.0, 
                        cos(radians(:lat)) * cos(radians(s.stop_lat)) * cos(radians(s.stop_lon) - radians(:lon)) + 
                        sin(radians(:lat)) * sin(radians(s.stop_lat))
                    ))
            )) <= :radiusMeters
            ORDER BY distanceMeters ASC
            LIMIT :limit
            """, nativeQuery = true)
    List<NearbyStopProjection> findNearbyStops(
            @Param("lat") double lat,
            @Param("lon") double lon,
            @Param("radiusMeters") double radiusMeters,
            @Param("limit") int limit
    );

    interface NearbyStopProjection {
        String getId();
        String getName();
        String getCode();
        String getPlatformCode();
        Double getLatitude();
        Double getLongitude();
        String getTownName();
        Integer getWheelchairBoarding();
        Double getDistanceMeters();
    }
}
