package pl.zabawa.optitransit.endpoints.controllers;

import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RestController;
import pl.zabawa.optitransit.endpoints.StopEndpoint;
import pl.zabawa.optitransit.models.dtos.StopDto;
import pl.zabawa.optitransit.services.StopService;

import java.util.List;

@RestController
@RequiredArgsConstructor
public class StopController implements StopEndpoint {

    private final StopService stopService;

    @Override
    public ResponseEntity<List<StopDto>> searchStops(String query, int limit) {
        return ResponseEntity.ok(stopService.searchStops(query, limit));
    }

    @Override
    public ResponseEntity<StopDto> getStopById(String id) {
        return stopService.getStopById(id)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @Override
    public ResponseEntity<List<StopDto>> getAllStops(int limit) {
        return ResponseEntity.ok(stopService.getAllStops(limit));
    }
}
