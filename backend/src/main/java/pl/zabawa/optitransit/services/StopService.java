package pl.zabawa.optitransit.services;

import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import pl.zabawa.optitransit.models.dtos.DepartureDto;
import pl.zabawa.optitransit.models.dtos.NearbyStopDto;
import pl.zabawa.optitransit.models.dtos.StopDto;
import pl.zabawa.optitransit.models.entities.StopEntity;
import pl.zabawa.optitransit.repositories.StopRepository;
import pl.zabawa.optitransit.repositories.StopTimeRepository;

import java.time.LocalTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class StopService {

    private final StopRepository stopRepository;
    private final StopTimeRepository stopTimeRepository;

    public List<StopDto> searchStops(String query, int limit) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        int sanitizedLimit = Math.clamp(limit, 1, 50);
        PageRequest pageRequest = PageRequest.of(0, sanitizedLimit);

        return stopRepository.findByNameContainingIgnoreCase(query.trim(), pageRequest)
                .stream()
                .map(this::mapToDto)
                .toList();
    }

    public Optional<StopDto> getStopById(String id) {
        return stopRepository.findById(id).map(this::mapToDto);
    }

    public List<StopDto> getAllStops(int limit) {
        int sanitizedLimit = Math.clamp(limit, 1, 100);
        return stopRepository.findAll(PageRequest.of(0, sanitizedLimit))
                .stream()
                .map(this::mapToDto)
                .toList();
    }

    public List<NearbyStopDto> getNearbyStops(double lat, double lon, double radiusMeters, int limit) {
        double sanitizedRadius = Math.clamp(radiusMeters, 50.0, 10000.0);
        int sanitizedLimit = Math.clamp(limit, 1, 50);

        List<StopRepository.NearbyStopProjection> nearbyStops = stopRepository.findNearbyStops(
                lat,
                lon,
                sanitizedRadius,
                sanitizedLimit
        );

        return nearbyStops.stream()
                .map(this::mapToNearbyDto)
                .toList();
    }

    public Optional<List<DepartureDto>> getDepartures(String stopId, String time, int limit) {
        if (!stopRepository.existsById(stopId)) {
            return Optional.empty();
        }

        String effectiveTime = (time != null && !time.isBlank())
                ? time.trim()
                : LocalTime.now(ZoneId.of("Europe/Warsaw")).format(DateTimeFormatter.ofPattern("HH:mm:ss"));

        int sanitizedLimit = Math.clamp(limit, 1, 50);

        List<StopTimeRepository.DepartureProjection> departures = stopTimeRepository.findUpcomingDepartures(
                stopId,
                effectiveTime,
                sanitizedLimit
        );

        List<DepartureDto> dtos = departures.stream()
                .map(p -> mapToDepartureDto(p, effectiveTime))
                .toList();

        return Optional.of(dtos);
    }

    private NearbyStopDto mapToNearbyDto(StopRepository.NearbyStopProjection projection) {
        return NearbyStopDto.builder()
                .id(projection.getId())
                .name(projection.getName())
                .code(projection.getCode())
                .platformCode(projection.getPlatformCode())
                .latitude(projection.getLatitude())
                .longitude(projection.getLongitude())
                .townName(projection.getTownName())
                .wheelchairBoarding(projection.getWheelchairBoarding())
                .distanceMeters(projection.getDistanceMeters())
                .build();
    }

    private DepartureDto mapToDepartureDto(StopTimeRepository.DepartureProjection projection, String effectiveTime) {
        String rawColor = projection.getRouteColor();
        String formattedColor = (rawColor != null && !rawColor.isBlank())
                ? (rawColor.startsWith("#") ? rawColor : "#" + rawColor)
                : "#000000";

        String rawTextColor = projection.getRouteTextColor();
        String formattedTextColor = (rawTextColor != null && !rawTextColor.isBlank())
                ? (rawTextColor.startsWith("#") ? rawTextColor : "#" + rawTextColor)
                : "#FFFFFF";

        long minutesToDeparture = calculateMinutesToDeparture(effectiveTime, projection.getDepartureTime());

        return DepartureDto.builder()
                .line(projection.getLine())
                .headsign(projection.getHeadsign())
                .routeType(projection.getRouteType())
                .routeColor(formattedColor)
                .routeTextColor(formattedTextColor)
                .departureTime(projection.getDepartureTime())
                .minutesToDeparture(minutesToDeparture)
                .wheelchairAccessible(projection.getWheelchairAccessible() != null && projection.getWheelchairAccessible() == 1)
                .tripId(projection.getTripId())
                .stopId(projection.getStopId())
                .build();
    }

    private long calculateMinutesToDeparture(String currentTimeStr, String departureTimeStr) {
        try {
            long currentMinutes = parseTimeToMinutes(currentTimeStr);
            long departureMinutes = parseTimeToMinutes(departureTimeStr);
            long diff = departureMinutes - currentMinutes;
            return Math.max(0, diff);
        } catch (Exception e) {
            return 0;
        }
    }

    private long parseTimeToMinutes(String timeStr) {
        if (timeStr == null || timeStr.isBlank()) {
            return 0;
        }
        String[] parts = timeStr.split(":");
        long hours = Long.parseLong(parts[0]);
        long minutes = parts.length > 1 ? Long.parseLong(parts[1]) : 0;
        return hours * 60 + minutes;
    }

    private StopDto mapToDto(StopEntity entity) {
        return StopDto.builder()
                .id(entity.getId())
                .name(entity.getName())
                .code(entity.getCode())
                .platformCode(entity.getPlatformCode())
                .latitude(entity.getLatitude())
                .longitude(entity.getLongitude())
                .townName(entity.getTownName())
                .wheelchairBoarding(entity.getWheelchairBoarding())
                .build();
    }
}
