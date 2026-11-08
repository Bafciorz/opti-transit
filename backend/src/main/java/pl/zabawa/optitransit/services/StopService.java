package pl.zabawa.optitransit.services;

import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import pl.zabawa.optitransit.models.dtos.StopDto;
import pl.zabawa.optitransit.models.entities.StopEntity;
import pl.zabawa.optitransit.repositories.StopRepository;

import java.util.List;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class StopService {

    private final StopRepository stopRepository;

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
